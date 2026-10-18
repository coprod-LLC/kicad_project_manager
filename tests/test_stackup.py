import hashlib
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from project_manager.stackup import (appearance_override, matching_stackups, parse_stackup, physical_summary,
                                    read_board_stackup, stackup_identity)
from project_manager.standards import StandardsError, load_catalog
from project_manager.standards_apply import apply_standards, plan_standards
from tests.standards_fixtures import detailed_catalog, saved_board


class StackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.folder = detailed_catalog(self.base / 'standards')
        self.catalog = load_catalog(self.folder)
        self.preset = next(p for p in self.catalog.stackups if p.id == 'example_six_layer')
        self.project, self.board = saved_board(self.base / 'project')
        self.store = self.base / 'settings'

    def test_ordered_sublayers_keep_their_own_properties_and_native_text(self):
        text = self.preset.files['stackup_file']
        stack = parse_stackup(text)
        plies = [layer for layer in stack.layers if layer.name == 'dielectric 3']
        self.assertEqual([(p.label, p.material, p.thickness_mm, p.epsilon_r, p.locked) for p in plies],
                         [('D3.1', 'Weave A', 0.24, 4.2, True), ('D3.2', 'Weave B', 0.2, 3.8, False)])
        self.assertAlmostEqual(stack.total_mm, 1.58)
        self.assertTrue(stack.impedance_controlled)
        self.assertEqual(stack.finish, 'ENIG')
        self.assertEqual(stack.native, text)
        self.assertEqual([layer.name for layer in stack.layers[:3]], ['F.SilkS', 'F.Paste', 'F.Mask'])

    def test_unspecified_material_is_not_invented_or_inherited(self):
        text = self.preset.files['stackup_file'].replace('(material "Weave B") (epsilon_r 3.8)', '')
        ply = next(p for p in parse_stackup(text).layers if p.label == 'D3.2')
        self.assertEqual(ply.material, '')
        self.assertIsNone(ply.epsilon_r)
        self.assertIsNone(parse_stackup('(stackup)').impedance_controlled)

    def test_invalid_duplicate_layers_and_nonfinite_properties_fail(self):
        for text in ['(stackup (layer "F.Cu" (type "copper") (thickness -1)))',
                     '(stackup (layer "F.Cu" (type "copper") (epsilon_r nan)))',
                     '(stackup (layer "F.Cu" (type "copper")) (layer "F.Cu" (type "copper")))',
                     '(stackup (layer "F.Cu" (type "copper") addsublayer (thickness 0.1)))',
                     '(stackup (dielectric_constraints perhaps))']:
            with self.subTest(text=text), self.assertRaises(StandardsError):
                parse_stackup(text)

    def test_apply_surfaces_sublayers_and_flag_and_repeat_without_drift(self):
        original_project = self.project.read_bytes()
        self.board.write_text(self.board.read_text().replace('(0 "F.Cu" signal)', '(0 "F.Cu" power "Supply")'))
        apply_standards(plan_standards(self.project, self.board, None, self.preset), self.store)
        actual = read_board_stackup(self.board)
        expected = parse_stackup(self.preset.files['stackup_file'])
        self.assertEqual(actual.physical.layers, expected.layers)
        self.assertTrue(actual.physical.impedance_controlled)
        self.assertAlmostEqual(actual.board_thickness_mm, actual.physical.total_mm)
        self.assertIn('(0 "F.Cu" power "Supply")', self.board.read_text())
        self.assertEqual(self.project.read_bytes(), original_project)
        self.assertFalse(plan_standards(self.project, self.board, None, self.preset).changes)

    def test_retained_masks_are_included_in_applied_thickness(self):
        self.board.write_text(self.board.read_text().replace('(setup (stackup',
                              '(setup (stackup (layer "F.Mask" (type "Top Solder Mask") (thickness 0.03))'))
        partial = next(p for p in self.catalog.stackups if p.id == 'two_layer')
        apply_standards(plan_standards(self.project, self.board, None, partial), self.store)
        actual = read_board_stackup(self.board)
        self.assertAlmostEqual(actual.board_thickness_mm, 1.63)
        self.assertIn('1.63 mm', physical_summary(actual.physical))
        self.assertFalse(plan_standards(self.project, self.board, None, partial).changes)

    def test_appearance_override_changes_only_colors_and_finish_and_is_recorded(self):
        selected = appearance_override(self.preset, mask='Green', silk='Yellow', finish='ENIG')
        before, after = parse_stackup(self.preset.files['stackup_file']), parse_stackup(selected.files['stackup_file'])
        self.assertEqual([p for p in before.layers if p.kind in ('copper', 'core', 'prepreg')],
                         [p for p in after.layers if p.kind in ('copper', 'core', 'prepreg')])
        self.assertEqual({p.color for p in after.layers if p.name.endswith('.Mask')}, {'Green'})
        self.assertEqual({p.color for p in after.layers if p.name.endswith('.SilkS')}, {'Yellow'})
        self.assertEqual(after.total_mm, before.total_mm)
        apply_standards(plan_standards(self.project, self.board, None, selected), self.store)
        provenance = json.loads((self.project.parent / 'kpm-standards.json').read_text())
        self.assertEqual(provenance['stackup']['appearance_overrides']['mask_color'], 'Green')
        self.assertEqual(provenance['stackup']['files']['stackup_file'],
                         hashlib.sha256(self.preset.files['stackup_file'].encode()).hexdigest())
        self.assertEqual(provenance['stackup']['applied_file_hashes']['stackup_file'],
                         hashlib.sha256(selected.files['stackup_file'].encode()).hexdigest())

    def test_metadata_is_validated_and_guidance_does_not_assign_nets(self):
        path = self.folder / self.preset.details['preset_file']
        original = path.read_text()
        for update in [{'manufacturer_recommended': 'yes'}, {'outer_copper_oz': -1},
                       {'source_url': 'file:///outside'}, {'recommendation_checked': 'yesterday'},
                       {'impedance_targets': [{'mode': 'differential', 'impedance_ohms': 100,
                                               'track_width_mm': 0.2, 'pair_gap_mm': 0}]}]:
            data = json.loads(original)
            data.update(update)
            path.write_text(json.dumps(data))
            with self.subTest(update=update), self.assertRaises(StandardsError):
                load_catalog(self.folder)
        path.write_text(original)

    def test_saved_construction_is_identified_without_provenance(self):
        current = read_board_stackup(self.board).physical
        matches = matching_stackups(current, self.catalog.stackups)
        self.assertEqual([match.preset.id for match in matches], ['two_layer'])
        self.assertTrue(matches[0].exact)
        self.assertEqual(stackup_identity(matches[0].preset), 'Two-layer example')
        self.assertEqual(matching_stackups(None, self.catalog.stackups), ())

    def test_changed_ply_does_not_inherit_an_applied_identity(self):
        apply_standards(plan_standards(self.project, self.board, None, self.preset), self.store)
        self.board.write_text(self.board.read_text().replace('(thickness 0.24 locked)', '(thickness 0.241 locked)'))
        self.assertEqual(matching_stackups(read_board_stackup(self.board).physical, self.catalog.stackups), ())
        original = parse_stackup(self.preset.files['stackup_file'])
        index = next(i for i, layer in enumerate(original.layers) if layer.label == 'D3.2')
        for change in ({'material': 'Different weave'}, {'epsilon_r': 4.1}, {'loss_tangent': 0.025}):
            layers = list(original.layers)
            layers[index] = replace(layers[index], **change)
            with self.subTest(change=change):
                self.assertEqual(matching_stackups(replace(original, layers=tuple(layers)), self.catalog.stackups), ())

    def test_surface_overrides_keep_identity_with_differences_disclosed(self):
        override = appearance_override(self.preset, mask='Green', silk='Yellow')
        current = parse_stackup(override.files['stackup_file'])
        matches = matching_stackups(current, self.catalog.stackups)
        self.assertEqual(len(matches), 1)
        self.assertEqual(stackup_identity(matches[0].preset), 'Example Fab · EX0616')
        self.assertFalse(matches[0].exact)
        exact = replace(override, id='green_variant')
        matches = matching_stackups(current, (*self.catalog.stackups, exact))
        self.assertEqual([(match.preset.id, match.exact) for match in matches], [('green_variant', True)])

    def test_identical_constructions_from_different_manufacturers_are_ambiguous(self):
        other = replace(self.preset, id='other_supplier',
                        details={**self.preset.details, 'manufacturer': 'Another Fab'})
        matches = matching_stackups(parse_stackup(self.preset.files['stackup_file']), (self.preset, other))
        self.assertEqual([stackup_identity(match.preset) for match in matches],
                         ['Example Fab · EX0616', 'Another Fab · EX0616'])
        self.assertTrue(all(match.exact for match in matches))

    def test_editor_settings_and_floating_point_noise_are_distinguished(self):
        current = parse_stackup(self.preset.files['stackup_file'])
        layers = list(current.layers)
        index = next(i for i, layer in enumerate(layers) if layer.label == 'D3.2')
        layers[index] = replace(layers[index], thickness_mm=0.20000000000000004)
        self.assertTrue(matching_stackups(replace(current, layers=tuple(layers)), (self.preset,))[0].exact)
        changed = replace(current, impedance_controlled=False)
        match = matching_stackups(changed, (self.preset,))[0]
        self.assertEqual(match.preset.id, self.preset.id)
        self.assertFalse(match.exact)


if __name__ == '__main__':
    unittest.main()
