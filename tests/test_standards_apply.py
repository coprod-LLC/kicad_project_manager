import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from project_manager import setup
from project_manager.standards import StandardsError, load_catalog
from project_manager.sexpr import atoms, forms
from project_manager.standards_apply import (
    COMPANY_BEGIN, COMPANY_END, apply_standards, merge_custom_rules, plan_standards,
)
from tests.standards_fixtures import catalog_folder, saved_board, write_stackup


def run_git(folder: Path, *args: str) -> None:
    subprocess.run(['git', '-C', str(folder), '-c', 'user.email=dev@example.com', '-c', 'user.name=Dev', *args],
                   check=True, capture_output=True, text=True)


class ApplyStandardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.folder = catalog_folder(self.base / 'standards')
        self.project, self.board = saved_board(self.base / 'project')
        self.store = self.base / 'settings'

    def four_layer_preset(self):
        native = '''(stackup
  (layer "F.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 1" (type "prepreg") (thickness 0.2))
  (layer "In1.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 2" (type "core") (thickness 1.16))
  (layer "In2.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 3" (type "prepreg") (thickness 0.2))
  (layer "B.Cu" (type "copper") (thickness 0.035))
  (copper_finish "ENIG"))
'''
        write_stackup(self.folder / 'stackups/four_layer.json', 'four', 'Four layer', native)
        return next(p for p in load_catalog(self.folder).stackups if p.id == 'four')

    def test_company_block_keeps_project_rules_and_is_idempotent(self):
        dru = self.board.with_suffix('.kicad_dru')
        dru.write_text('(version 1)\n(rule "Courtyard override"\n  (constraint courtyard_clearance (min -1mm)))\n'
                       '(rule "Voltage domain" (constraint clearance (min 0.5mm)))\n')
        original_rules = dru.read_text().split('(version 1)\n', 1)[1]
        catalog = load_catalog(self.folder)
        plan = plan_standards(self.project, self.board, catalog.rules[0], None, catalog)
        apply_standards(plan, self.store)
        text = dru.read_text()
        self.assertLess(text.index(COMPANY_BEGIN), text.index('Courtyard override'))
        self.assertLess(text.index(COMPANY_END), text.index('Courtyard override'))
        self.assertIn(original_rules, text)
        self.assertEqual(text.count(COMPANY_BEGIN), 1)
        self.assertIn('Test domain', text[text.index(COMPANY_BEGIN):text.index(COMPANY_END)])
        project = json.loads(self.project.read_text())
        self.assertEqual(project['unrelated'], 'keep')
        self.assertEqual(project['board']['design_settings']['rules']['min_track_width'], 0.1)
        self.assertEqual(project['board']['design_settings']['rules']['min_clearance'], 0.2)
        self.assertEqual(project['net_settings']['classes'][0]['name'], 'Default')
        self.assertEqual(project['net_settings']['classes'][1]['name'], 'SIGNAL')
        again = plan_standards(self.project, self.board, catalog.rules[0], None, catalog)
        self.assertEqual(again.changes, ())
        self.assertIsNone(apply_standards(again, self.store))
        self.assertEqual(dru.read_text(), text)

    def test_existing_block_is_replaced_in_place(self):
        dru = self.board.with_suffix('.kicad_dru')
        dru.write_text('(version 1)\n(rule "Before" (constraint clearance (min 1mm)))\n'
                       + COMPANY_BEGIN + '\n(rule "Old company" (constraint clearance (min 9mm)))\n' + COMPANY_END
                       + '\n(rule "After" (constraint clearance (min 2mm)))\n')
        catalog = load_catalog(self.folder)
        apply_standards(plan_standards(self.project, self.board, catalog.rules[0], None), self.store)
        text = dru.read_text()
        self.assertLess(text.index('Before'), text.index(COMPANY_BEGIN))
        self.assertLess(text.index(COMPANY_END), text.index('After'))
        self.assertNotIn('Old company', text)
        self.assertIn('(rule "Before" (constraint clearance (min 1mm)))', text)
        self.assertIn('(rule "After" (constraint clearance (min 2mm)))', text)

    def test_duplicate_and_missing_markers_are_refused(self):
        dru = self.board.with_suffix('.kicad_dru')
        original = dru.read_bytes()
        catalog = load_catalog(self.folder)
        dru.write_text('(version 1)\n' + COMPANY_BEGIN + '\n' + COMPANY_BEGIN + '\n' + COMPANY_END + '\n')
        with self.assertRaisesRegex(StandardsError, 'broken'):
            plan_standards(self.project, self.board, catalog.rules[0], None)
        preset = catalog.rules[0]
        dru.write_text(merge_custom_rules('', preset).replace(COMPANY_BEGIN, '# misplaced').replace(COMPANY_END, '# misplaced end'))
        with self.assertRaisesRegex(StandardsError, 'markers are missing'):
            plan_standards(self.project, self.board, preset, None)
        self.assertNotEqual(dru.read_bytes(), original)

    def test_project_rule_name_collision_refuses_without_writes(self):
        rules = self.folder / 'rules.kicad_dru'
        rules.write_text(rules.read_text() + '(rule "Other" (constraint clearance (min 0.2mm)))\n')
        dru = self.board.with_suffix('.kicad_dru')
        dru.write_text('(version 1)\n(rule "Test domain" (constraint clearance (min 1mm)))\n')
        before = {path: path.read_bytes() for path in (self.project, self.board, dru)}
        catalog = load_catalog(self.folder)
        with self.assertRaisesRegex(StandardsError, 'Test domain'):
            plan_standards(self.project, self.board, catalog.rules[0], None)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_crlf_project_rules_keep_their_line_endings(self):
        dru = self.board.with_suffix('.kicad_dru')
        dru.write_bytes(b'(version 1)\r\n(rule "After" (constraint clearance (min 1mm)))\r\n')
        catalog = load_catalog(self.folder)
        text = merge_custom_rules(dru.read_bytes().decode('utf-8'), catalog.rules[0])
        self.assertIn(b'(rule "After" (constraint clearance (min 1mm)))\r\n', text.encode('utf-8'))
        self.assertNotIn('\n(rule "After"', text.replace('\r\n', ''))

    def test_identical_existing_prefix_can_be_adopted_without_moving_project_exceptions(self):
        catalog = load_catalog(self.folder)
        preset = catalog.rules[0]
        dru = self.board.with_suffix('.kicad_dru')
        exception = '(rule "Local exception" (constraint courtyard_clearance (min -1mm)))\n'
        dru.write_text('# Existing header stays\n' + preset.files['custom_rules_file'] + exception)
        plan = plan_standards(self.project, self.board, preset, None, catalog)
        self.assertTrue(plan.adopts_existing_rules)
        self.assertIn('identical existing rule prefix', plan.summary)
        apply_standards(plan, self.store)
        saved = dru.read_text()
        self.assertTrue(saved.startswith('# Existing header stays\n'))
        self.assertEqual(saved.count('(rule "Test domain"'), 1)
        self.assertLess(saved.index(COMPANY_END), saved.index(exception))
        again = plan_standards(self.project, self.board, preset, None, catalog)
        self.assertFalse(again.adopts_existing_rules)
        self.assertFalse(again.changes)

    def test_existing_rules_with_different_values_or_interleaved_priority_are_not_adopted(self):
        catalog = load_catalog(self.folder)
        preset = catalog.rules[0]
        dru = self.board.with_suffix('.kicad_dru')
        for text in [preset.files['custom_rules_file'].replace('0.2mm', '0.8mm'),
                     '(version 1)\n(rule "Local first" (constraint clearance (min 1mm)))\n'
                     + preset.files['custom_rules_file'].split('(version 1)', 1)[1]]:
            with self.subTest(text=text):
                dru.write_text(text)
                before = dru.read_bytes()
                with self.assertRaises(StandardsError):
                    plan_standards(self.project, self.board, preset, None)
                self.assertEqual(dru.read_bytes(), before)

    def test_net_assignments_and_other_classes_stay(self):
        data = json.loads(self.project.read_text())
        data['net_settings']['classes'].append({'name': 'HV', 'clearance': 0.5, 'pcb_color': 'rgb(1, 2, 3)'})
        data['net_settings']['netclass_assignments'] = {'VBAT': ['HV']}
        data['net_settings']['netclass_patterns'] = [{'pattern': 'VBAT*', 'netclass': 'HV'}]
        self.project.write_text(json.dumps(data, indent=2) + '\n')
        catalog = load_catalog(self.folder)
        apply_standards(plan_standards(self.project, self.board, catalog.rules[0], None), self.store)
        saved = json.loads(self.project.read_text())
        hv = next(item for item in saved['net_settings']['classes'] if item['name'] == 'HV')
        self.assertEqual(hv['pcb_color'], 'rgb(1, 2, 3)')
        self.assertEqual(hv['clearance'], 0.5)
        self.assertEqual(saved['net_settings']['netclass_assignments'], {'VBAT': ['HV']})
        self.assertEqual(saved['net_settings']['netclass_patterns'], [{'pattern': 'VBAT*', 'netclass': 'HV'}])

    def test_changed_shared_prefix_requires_explicit_adoption_proposal(self):
        catalog = load_catalog(self.folder)
        preset = catalog.rules[0]
        dru = self.board.with_suffix('.kicad_dru')
        local = '(rule "Local last" (constraint clearance (min 1mm)))\n'
        dru.write_text(preset.files['custom_rules_file'].replace('0.2mm', '0.8mm') + local)
        before = dru.read_bytes()
        with self.assertRaises(StandardsError):
            plan_standards(self.project, self.board, preset, None)
        plan = plan_standards(self.project, self.board, preset, None, catalog, allow_rule_adoption=True)
        self.assertTrue(plan.adopts_existing_rules)
        self.assertIn('Existing shared rules that will change', plan.summary)
        self.assertIn('-  (constraint clearance (min 0.8mm)))', plan.diff())
        self.assertEqual(dru.read_bytes(), before)
        apply_standards(plan, self.store)
        self.assertIn(local, dru.read_text())
        self.assertFalse(plan_standards(self.project, self.board, preset, None, catalog).changes)

    def test_stackup_keeps_mask_layers_nicknames_and_board_body(self):
        self.board.write_text(
            '(kicad_pcb (version 20241229) (generator "pcbnew")\n'
            '(general (thickness 1.6) (legacy_teardrops no))\n'
            '(layers\n'
            '\t(0 "F.Cu" signal "L1")\n'
            '\t(2 "B.Cu" signal "L2")\n'
            '\t(31 "F.CrtYd" user "F.Courtyard")\n'
            ')\n'
            '(setup (stackup\n'
            '\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))\n'
            '\t(layer "F.Cu" (type "copper") (thickness 0.035))\n'
            '\t(layer "dielectric 1" (type "core") (thickness 1.53) (material "FR4"))\n'
            '\t(layer "B.Cu" (type "copper") (thickness 0.035))\n'
            '\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))\n'
            '\t(copper_finish "HASL")\n'
            '\t(dielectric_constraints yes))\n'
            '(pad_to_mask_clearance 0.05))\n'
            '(footprint "Resistor" (uuid "UUID-KEEP") (attr smd))\n)\n')
        catalog = load_catalog(self.folder)
        apply_standards(plan_standards(self.project, self.board, None, catalog.stackups[0]), self.store)
        text = self.board.read_text()
        self.assertIn('(uuid "UUID-KEEP")', text)
        self.assertIn('(31 "F.CrtYd" user "F.Courtyard")', text)
        self.assertIn('(0 "F.Cu" signal "L1")', text)
        self.assertIn('Top Solder Mask', text)
        self.assertIn('(dielectric_constraints yes)', text)
        self.assertIn('(copper_finish "ENIG")', text)
        self.assertNotIn('HASL', text)
        self.assertIn('(legacy_teardrops no)', text)

    def test_used_copper_layer_is_not_removed(self):
        apply_standards(plan_standards(self.project, self.board, None, self.four_layer_preset()), self.store)
        self.board.write_text(self.board.read_text().replace(
            '(footprint "a (# not a comment)" (property "Reference" "R1"))',
            '(segment (start 0 0) (end 1 1) (width 0.2) (layer "In1.Cu") (net 1))\n'
            '(footprint "a (# not a comment)" (property "Reference" "R1"))'))
        before = self.board.read_bytes()
        catalog = load_catalog(self.folder)
        two_layer = next(p for p in catalog.stackups if p.id == 'two_layer')
        with self.assertRaisesRegex(StandardsError, 'In1.Cu'):
            plan_standards(self.project, self.board, None, two_layer)
        self.assertEqual(self.board.read_bytes(), before)

    def test_inner_layers_can_be_added(self):
        stack = self.four_layer_preset()
        self.board.write_text(self.board.read_text().replace(
            '(footprint "a (# not a comment)" (property "Reference" "R1"))',
            '(footprint "a (# not a comment)" (property "Reference" "R1") (uuid "UUID-KEEP"))'))
        apply_standards(plan_standards(self.project, self.board, None, stack), self.store)
        text = self.board.read_text()
        layers = next(node for name, node in forms(text, children=True) if name == 'layers')
        copper = [atoms(node)[:2] for _name, node in forms(layers, children=True) if atoms(node)[1].endswith('.Cu')]
        self.assertEqual(copper, [['0', 'F.Cu'], ['4', 'In1.Cu'], ['6', 'In2.Cu'], ['2', 'B.Cu']])
        self.assertIn('(uuid "UUID-KEEP")', text)
        self.assertIn('(thickness 1.7)', text)

    def test_editor_lock_and_external_edit_do_not_leave_a_partial_apply(self):
        catalog = load_catalog(self.folder)
        plan = plan_standards(self.project, self.board, catalog.rules[0], catalog.stackups[0], catalog)
        lock = self.project.parent / '~sample.kicad_pcb.lck'
        lock.write_text('{"hostname":"test"}\n')
        with self.assertRaisesRegex(StandardsError, 'already has this project open|still has this project open'):
            apply_standards(plan, self.store)
        self.assertIn('"min_clearance": 0.1', self.project.read_text())
        lock.unlink()
        plan = plan_standards(self.project, self.board, catalog.rules[0], None, catalog)
        original = self.project.read_bytes()
        replace = setup._replace

        def change_dru(path, data, mode):
            if path.name.endswith('.kicad_dru'):
                path.write_bytes(b'(version 1)\n(rule "External" (constraint clearance (min 3mm)))\n')
                raise OSError('simulated conflict')
            replace(path, data, mode)

        with patch.object(setup, '_replace', change_dru), self.assertRaisesRegex(StandardsError, 'Restore|original files'):
            apply_standards(plan, self.store)
        self.assertEqual(self.project.read_bytes(), original)

    def test_source_commit_is_recorded_when_the_catalog_is_a_checkout(self):
        if subprocess.run(['git', '--version'], capture_output=True).returncode != 0:
            self.skipTest('git unavailable')
        run_git(self.folder, 'init')
        run_git(self.folder, 'add', '.')
        run_git(self.folder, 'commit', '-m', 'standards')
        catalog = load_catalog(self.folder)
        apply_standards(plan_standards(self.project, self.board, catalog.rules[0], None, catalog), self.store)
        provenance = json.loads((self.project.parent / 'kpm-standards.json').read_text())
        self.assertEqual(provenance['rules']['preset_id'], 'example_rules')
        self.assertRegex(provenance['rules']['source_commit'], r'^[0-9a-f]{7,64}$')
        self.assertNotIn('source_dirty', provenance['rules'])
        self.assertIn(provenance['rules']['source_commit'], self.board.with_suffix('.kicad_dru').read_text())
        (self.folder / 'catalog.json').write_text((self.folder / 'catalog.json').read_text() + '\n')
        plan = plan_standards(self.project, self.board, catalog.rules[0], None, catalog)
        self.assertTrue(any(b'source_dirty' in change.after for change in plan.changes))

if __name__ == '__main__':
    unittest.main()
