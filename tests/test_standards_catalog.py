"""The catalog discovers independently removable stackup documents."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from project_manager.standards import StandardsError, load_catalog
from project_manager.standards_apply import apply_standards, plan_standards
from tests.standards_fixtures import STACKUP, catalog_folder, saved_board, write_stackup


class CatalogDocumentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = catalog_folder(self.base / 'standards')
        self.document = self.root / 'stackups/two_layer.json'

    def snapshot(self):
        return {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_adding_renaming_and_deleting_only_one_file_changes_the_catalog(self):
        original = self.snapshot()
        path = write_stackup(self.root / 'stackups/maker/new.json', 'new', 'New', STACKUP)
        before_load = self.snapshot()
        self.assertEqual({p.id for p in load_catalog(self.root).stackups}, {'two_layer', 'new'})
        self.assertEqual(before_load, self.snapshot())
        renamed = path.with_name('renamed.json')
        path.rename(renamed)
        self.assertEqual({p.id for p in load_catalog(self.root).stackups}, {'two_layer', 'new'})
        renamed.unlink()
        self.assertEqual([p.id for p in load_catalog(self.root).stackups], ['two_layer'])
        self.assertEqual(original, self.snapshot())

    def test_copper_summaries_and_neutral_declarations_are_derived(self):
        preset = load_catalog(self.root).stackups[0]
        self.assertEqual(preset.details['copper_layer_count'], 2)
        self.assertAlmostEqual(preset.details['board_thickness_mm'], 1.6)
        self.assertEqual(preset.details['copper'], [{'layer': 'F.Cu', 'thickness_mm': 0.035},
                                                   {'layer': 'B.Cu', 'thickness_mm': 0.035}])
        self.assertEqual(preset.files['copper_layers_file'], '(layers\n  (0 "F.Cu" signal)\n  (2 "B.Cu" signal)\n)\n')
        self.assertNotIn('stackup', preset.details)

    def test_applied_provenance_hashes_document_metadata_and_survives_preset_deletion(self):
        project, board = saved_board(self.base / 'project')
        store = self.base / 'settings'
        catalog = load_catalog(self.root)
        apply_standards(plan_standards(project, board, None, catalog.stackups[0], catalog), store)
        provenance = project.parent / 'kpm-standards.json'
        first = json.loads(provenance.read_text())['stackup']
        self.assertEqual(first['preset_file'], 'stackups/two_layer.json')
        self.assertEqual(first['files']['preset_file'], hashlib.sha256(self.document.read_bytes()).hexdigest())
        board_bytes = board.read_bytes()
        data = json.loads(self.document.read_text())
        data['notes'] = 'New routing source evidence'
        self.document.write_text(json.dumps(data))
        catalog = load_catalog(self.root)
        plan = plan_standards(project, board, None, catalog.stackups[0], catalog)
        self.assertEqual([c.relative for c in plan.changes], ['kpm-standards.json'])
        apply_standards(plan, store)
        second = json.loads(provenance.read_text())['stackup']
        self.assertNotEqual(first['files']['preset_file'], second['files']['preset_file'])
        self.assertEqual(first['files']['stackup_file'], second['files']['stackup_file'])
        self.assertFalse(plan_standards(project, board, None, catalog.stackups[0], catalog).changes)
        self.document.unlink()
        snapshot = {p: p.read_bytes() for p in project.parent.iterdir() if p.is_file()}
        self.assertEqual(load_catalog(self.root).stackups, ())
        self.assertEqual(snapshot, {p: p.read_bytes() for p in snapshot})
        self.assertEqual(board_bytes, board.read_bytes())

    def test_empty_stackup_directory_can_be_removed_from_a_rules_catalog(self):
        self.document.unlink()
        self.document.parent.rmdir()
        self.assertEqual(len(load_catalog(self.root).rules), 1)

    def test_duplicate_ids_in_different_files_are_rejected(self):
        write_stackup(self.root / 'stackups/another/copy.json', 'two_layer', 'Copy', STACKUP)
        with self.assertRaisesRegex(StandardsError, 'unique'):
            load_catalog(self.root)

    def test_invalid_json_is_reported_with_its_filename(self):
        for text in ('{', '[]', '{"id": "x", "id": "y"}', '{"value": NaN}'):
            with self.subTest(text=text):
                self.document.write_text(text)
                with self.assertRaisesRegex(StandardsError, 'stackups/two_layer.json'):
                    load_catalog(self.root)

    def test_invalid_versions_types_and_external_file_fields_are_rejected(self):
        original = json.loads(self.document.read_text())
        for updates in ({'schema_version': 2}, {'schema_version': True}, {'kicad_major': 10},
                        {'kicad_major': True}, {'stackup': ''}, {'stackup': []}, {'stackup': [None]},
                        {'stackup': ['(stackup\n)']}, {'source_evidence': []},
                        {'stackup_file': '../outside'}, {'copper_layers_file': 'layers.sexpr'},
                        {'preset_file': 'other.json'}):
            with self.subTest(updates=updates):
                self.document.write_text(json.dumps({**original, **updates}))
                with self.assertRaisesRegex(StandardsError, 'stackups/two_layer.json'):
                    load_catalog(self.root)

    def test_invalid_physical_layers_are_rejected_before_application(self):
        for native in (STACKUP.replace('0.035', '0'), STACKUP.replace('B.Cu', 'In1.Cu'),
                       STACKUP.replace('1.53', 'NaN'), STACKUP.replace('0.035', '1e308'),
                       STACKUP[:-3], '(stackup)'):
            with self.subTest(native=native):
                write_stackup(self.document, 'two_layer', 'Example', native)
                with self.assertRaisesRegex(StandardsError, 'stackups/two_layer.json'):
                    load_catalog(self.root)

    def test_symlinked_document_directory_and_root_are_rejected(self):
        outside = self.base / 'outside'
        outside.mkdir()
        for relative, target in (('linked.json', self.document), ('maker', outside)):
            path = self.document.parent / relative
            try:
                path.symlink_to(target, target_is_directory=target.is_dir())
            except OSError:
                self.skipTest('Symlinks unavailable')
            with self.subTest(relative=relative), self.assertRaisesRegex(StandardsError, 'Symbolic'):
                load_catalog(self.root)
            path.unlink()
        directory = self.root / 'stackups'
        moved = self.base / 'moved'
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(StandardsError, 'Symbolic'):
            load_catalog(self.root)

    def test_document_count_and_file_size_are_bounded(self):
        for index in range(100):
            write_stackup(self.root / f'stackups/{index}.json', f'example_{index}', 'Example', STACKUP)
        with self.assertRaisesRegex(StandardsError, 'at most 100'):
            load_catalog(self.root)
        for path in self.document.parent.glob('*.json'):
            if path != self.document:
                path.unlink()
        self.document.write_text(' ' * (2 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(StandardsError, 'smaller'):
            load_catalog(self.root)

    def test_unknown_native_fields_comments_and_source_evidence_survive_loading(self):
        native = STACKUP.replace('(copper_finish', '# saved comment\n  (edge_plating yes)\n  (copper_finish')
        evidence = {'captured_on': '2026-01-01', 'construction': {'manufacturer_field': [1, 2, 3]}}
        write_stackup(self.document, 'two_layer', 'Example', native, source_evidence=evidence)
        preset = load_catalog(self.root).stackups[0]
        self.assertEqual(preset.files['stackup_file'], native)
        self.assertEqual(preset.details['source_evidence'], evidence)


if __name__ == '__main__':
    unittest.main()
