import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from project_manager.preferences import load_standards, load_template, save_settings, save_template
from project_manager.sexpr import ExpressionError, atoms, canonical, forms
from project_manager.standards import StandardsError, compare, load_catalog, preset_details
from tests.standards_fixtures import catalog_folder, saved_board


class StandardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.folder = catalog_folder(self.base / 'standards')
        self.project, self.board = saved_board(self.base / 'project')

    def edit(self, mutate):
        path = self.folder / 'catalog.json'
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data))

    def test_preferences_merge_and_preserve_template_and_unknown_settings(self):
        store = self.base / 'settings'
        save_settings({'future_setting': 3, 'standards_directory': str(self.folder)}, store)
        save_template('/template', store)
        self.assertEqual(load_standards(store), str(self.folder))
        self.assertEqual(load_template(store), '/template')
        self.assertEqual(json.loads((store / 'settings.json').read_text())['future_setting'], 3)
        (store / 'settings.json').write_text('invalid')
        with self.assertRaises(ValueError):
            save_template('/other', store)
        self.assertEqual((store / 'settings.json').read_text(), 'invalid')

    def test_catalog_loads_and_detects_dependencies_from_rules(self):
        catalog = load_catalog(self.folder)
        self.assertEqual(catalog.rules[0].details['required_netclasses'], ['SIGNAL'])
        self.assertEqual(catalog.rules[0].details['referenced_rule_areas'], ['Protected'])
        self.assertEqual(catalog.rules[0].details['referenced_net_names'], ['FRAME'])
        self.assertIn('Draft sample', preset_details(catalog.stackups[0]))

    def test_compare_reports_dependencies_and_diff_without_any_writes(self):
        before = {p: p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        catalog = load_catalog(self.folder)
        report = compare(self.project, self.board, catalog.rules[0], catalog.stackups[0])
        self.assertIn('Netclasses missing from the saved project: SIGNAL', report.summary)
        self.assertIn('Named areas missing from the saved project: none', report.summary)
        self.assertIn('Named nets missing from the saved project: none', report.summary)
        self.assertIn('Copper layers: 2 → 2', report.summary)
        self.assertIn('"min_clearance": 0.2', report.diff)
        self.assertIn('existing exception', report.diff)
        self.assertNotIn('-# existing exception', report.diff)
        self.assertIn('company-rules block', report.summary)
        self.assertNotIn('.kicad_pcb physical stackup', report.diff)
        self.assertEqual(before, {p: p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_already_matching_stackup_and_whitespace_have_no_diff(self):
        catalog = load_catalog(self.folder)
        self.board.write_text(self.board.read_text().replace('(type "copper")', '(type\n "copper")'))
        report = compare(self.project, self.board, None, catalog.stackups[0])
        self.assertIn('already match', report.summary)
        self.assertNotIn('sample.kicad_pcb', report.diff)
        self.assertIn('kpm-standards.json', report.diff)

    def test_missing_dru_and_areas_are_explicit(self):
        self.board.with_suffix('.kicad_dru').unlink()
        self.board.write_text(self.board.read_text().replace('Protected', 'Other').replace('FRAME', 'Other'))
        catalog = load_catalog(self.folder)
        report = compare(self.project, self.board, catalog.rules[0], None)
        self.assertIn('Named areas missing from the saved project: Protected', report.summary)
        self.assertIn('Named nets missing from the saved project: FRAME', report.summary)
        self.assertIn('+(version 1)', report.diff)

    def test_footprint_rule_area_is_found(self):
        text = self.board.read_text().replace('(zone (name "Protected") (keepout (tracks not_allowed)))', '')
        self.board.write_text(text.replace('(property "Reference" "R1")', '(zone (name "Protected"))'))
        catalog = load_catalog(self.folder)
        self.assertIn('Named areas missing from the saved project: none',
                      compare(self.project, self.board, catalog.rules[0], None).summary)

    def test_unselected_categories_are_not_compared(self):
        catalog = load_catalog(self.folder)
        report = compare(self.project, self.board, catalog.rules[0], None)
        self.assertNotIn('Stackup:', report.summary)
        with self.assertRaisesRegex(StandardsError, 'Select'):
            compare(self.project, self.board, None, None)

    def test_paths_cannot_escape_catalog(self):
        original = (self.folder / 'catalog.json').read_bytes()
        for value in ('../outside', '/tmp/outside', 'C:/outside', '..\\outside', ''):
            with self.subTest(value=value):
                self.edit(lambda d: d['rules'][0].update(custom_rules_file=value))
                with self.assertRaises(StandardsError):
                    load_catalog(self.folder)
                (self.folder / 'catalog.json').write_bytes(original)

    def test_symlinks_are_rejected(self):
        path = self.folder / 'rules.kicad_dru'
        target = self.base / 'outside'
        target.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest('Symlinks unavailable')
        with self.assertRaisesRegex(StandardsError, 'Symbolic'):
            load_catalog(self.folder)

    def test_unknown_versions_duplicate_ids_and_bad_types_rejected(self):
        original = (self.folder / 'catalog.json').read_bytes()
        for mutate in [lambda d: d.update(schema_version=1), lambda d: d.update(schema_version=3),
                       lambda d: d.update(schema_version=True), lambda d: d.update(stackups=[]),
                       lambda d: d.update(kicad_major=10), lambda d: d['rules'].append(d['rules'][0]),
                       lambda d: d['rules'][0].update(id='BAD'), lambda d: d['rules'][0].update(status='unknown'),
                       lambda d: d.update(rules={}), lambda d: d['rules'][0].update(required_netclasses='bad')]:
            mutate_name = repr(mutate)
            with self.subTest(mutate=mutate_name):
                self.edit(mutate)
                with self.assertRaises(StandardsError):
                    load_catalog(self.folder)
                (self.folder / 'catalog.json').write_bytes(original)

    def test_duplicate_json_keys_and_nonfinite_constraints_rejected(self):
        path = self.folder / 'constraints.json'
        for text in ['{"board": {}, "board": {}}', '{"board":{"design_settings":{"rules":{"min_clearance":NaN}}}}']:
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaises(StandardsError):
                    load_catalog(self.folder)

    def test_constraints_cannot_include_other_project_data(self):
        path = self.folder / 'constraints.json'
        data = json.loads(path.read_text())
        data['net_settings'] = {'classes': []}
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(StandardsError, 'only'):
            load_catalog(self.folder)

    def test_stackup_documents_reject_redundant_summaries(self):
        path = self.folder / 'stackups/two_layer.json'
        original = path.read_bytes()
        for field, value in [('board_thickness_mm', 1.8), ('copper_layer_count', 4), ('copper', [])]:
            with self.subTest(field=field):
                data = json.loads(original)
                data[field] = value
                path.write_text(json.dumps(data))
                with self.assertRaises(StandardsError):
                    load_catalog(self.folder)
                path.write_bytes(original)

    def test_multiple_dielectric_sublayers_are_counted(self):
        path = self.folder / 'stackups/two_layer.json'
        path.write_text(path.read_text().replace('(thickness 1.53)', '(thickness 0.53) addsublayer (thickness 1.0)'))
        self.assertEqual(len(load_catalog(self.folder).stackups), 1)

    def test_bad_and_newer_board_formats_rejected(self):
        catalog = load_catalog(self.folder)
        original = self.board.read_text()
        for text in [original[:-2], original.replace('20241229', '20260101'), '(wrong)']:
            with self.subTest(text=text[:30]):
                self.board.write_text(text)
                with self.assertRaises(StandardsError):
                    compare(self.project, self.board, None, catalog.stackups[0])


class ExpressionTests(unittest.TestCase):
    def test_quotes_comments_and_parentheses_do_not_change_boundaries(self):
        text = '(root # ) ignored\n (name "quoted (name) # inside") (other "a\\\"b"))'
        nodes = list(forms(text, children=True))
        self.assertEqual([k for k, _ in nodes], ['name', 'other'])
        self.assertEqual(atoms(nodes[1][1]), ['other', 'a"b'])
        self.assertEqual(canonical('(a (b c))'), canonical('(a\n  (b   c))'))

    def test_malformed_expressions_fail(self):
        for text in ['(a', '(a))', '(a "unterminated)', 'garbage (a)', '(a) (b)']:
            with self.subTest(text=text), self.assertRaises(ExpressionError):
                list(forms(text, children=True))


if __name__ == '__main__':
    unittest.main()
