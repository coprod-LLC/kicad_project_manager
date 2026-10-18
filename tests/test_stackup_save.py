from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from project_manager.stackup import parse_stackup
from project_manager.stackup_save import capture_saved_stackup, prepare_preset, save_preset
from project_manager.standards import StandardsError, load_catalog
from tests.standards_fixtures import STACKUP, LAYERS, catalog_folder, detailed_catalog, saved_board, write_stackup


class SavePresetTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.folder = catalog_folder(self.base / 'standards')
        self.existing = self.folder / 'stackups/two_layer.json'
        self.existing.unlink()
        self.project, self.board = saved_board(self.base / 'project')
        self.capture = capture_saved_stackup(self.board)
        self.catalog = load_catalog(self.folder)
        self.metadata = {'manufacturer': 'Example Fab', 'name': 'Example construction',
                         'manufacturer_stackup_id': 'EX0216', 'nominal_thickness_mm': 1.6}

    def snapshot(self, root=None):
        return {p.relative_to(root or self.base): p.read_bytes()
                for p in (root or self.base).rglob('*') if p.is_file()}

    def existing_preset(self, native=STACKUP, **metadata):
        write_stackup(self.existing, 'existing', 'Existing construction', native, **metadata)
        self.catalog = load_catalog(self.folder)

    def plan(self, **metadata):
        return prepare_preset(self.capture, self.catalog, {**self.metadata, **metadata})

    def test_save_changes_one_file_and_retains_the_current_schema_without_local_paths_or_status(self):
        plan = self.plan(source_url='https://example.org/stackup', manufacturer_recommended=False,
                         preferred=True, notes='Alternative manufacturer option',
                         impedance_targets=[{'mode': 'differential', 'impedance_ohms': 100,
                                             'track_width_mm': 0.2, 'pair_gap_mm': 0.15,
                                             'layer': 'F.Cu', 'reference_layer': 'B.Cu', 'geometry': 'microstrip'}])
        before = self.snapshot()
        result = save_preset(plan)
        after = self.snapshot()
        relative = Path('standards') / plan.relative
        self.assertEqual(after.keys() - before.keys(), {relative})
        self.assertTrue(all(after[p] == contents for p, contents in before.items()))
        document = json.loads(after[relative])
        self.assertEqual(document['schema_version'], 1)
        self.assertEqual(document['kicad_major'], 9)
        self.assertNotIn('status', document)
        for key in ('copper', 'copper_layer_count', 'board_thickness_mm', 'stackup_file', 'copper_layers_file'):
            self.assertNotIn(key, document)
        self.assertIs(document['manufacturer_recommended'], False)
        self.assertIs(document['preferred'], True)
        self.assertNotIn(str(self.base), plan.text)
        self.assertNotIn(self.board.name, plan.text)
        self.assertEqual(document['source_evidence']['source_page'], 'https://example.org/stackup')
        self.assertEqual(len(result.stackups), 1)
        self.assertEqual(result.stackups[0].status, '')
        self.assertIn('Example construction', result.stackups[0].name)
        self.assertEqual(parse_stackup(result.stackups[0].files['stackup_file']).layers, self.capture.physical.layers)
        self.assertFalse(list(self.folder.rglob('*.tmp')))

    def test_native_sublayers_surfaces_and_unknown_properties_are_preserved(self):
        root = detailed_catalog(self.base / 'other')
        source = next(p for p in load_catalog(root).stackups if p.id == 'example_six_layer')
        native = source.files['stackup_file'].replace('(copper_finish', '(edge_plating yes)\n(copper_finish')
        board_text = self.board.read_text(encoding='utf-8').replace(STACKUP, native).replace(
            LAYERS, source.files['copper_layers_file'])
        self.board.write_bytes(board_text.replace('\n', '\r\n').encode('utf-8'))
        self.capture = capture_saved_stackup(self.board)
        result = save_preset(self.plan())
        saved = result.stackups[0].files['stackup_file']
        self.assertEqual(saved.strip(), self.capture.physical.native.replace('\r\n', '\n').strip())
        self.assertNotIn('\r', saved)
        self.assertIn('(edge_plating yes)', saved)
        self.assertEqual(parse_stackup(saved).layers, parse_stackup(native).layers)

    def test_same_manufacturer_and_code_are_blocked_even_if_geometry_differs(self):
        self.existing_preset(STACKUP.replace('1.53', '0.9'), manufacturer='example FAB',
                             manufacturer_stackup_id='EX - 0216')
        plan = self.plan()
        self.assertTrue(plan.matches[0].blocking)
        self.assertIn('code', plan.matches[0].reason)
        before = self.snapshot()
        with self.assertRaisesRegex(StandardsError, 'already exists'):
            save_preset(plan, allow_similar=True)
        self.assertEqual(before, self.snapshot())

    def test_surface_and_finish_differences_do_not_create_another_same_manufacturer_construction(self):
        self.existing_preset(STACKUP.replace('ENIG', 'HASL'), manufacturer='Example Fab')
        plan = self.plan()
        self.assertTrue(plan.matches[0].blocking)
        self.assertEqual(plan.matches[0].differences, ())

    def test_identical_geometry_from_another_supplier_requires_review_but_can_be_saved(self):
        self.existing_preset(manufacturer='Another Fab')
        plan = self.plan()
        self.assertFalse(plan.matches[0].blocking)
        with self.assertRaisesRegex(StandardsError, 'Review'):
            save_preset(plan)
        self.assertEqual(len(save_preset(plan, allow_similar=True).stackups), 2)

    def test_nearby_dimensions_show_specific_differences_and_require_review(self):
        self.existing_preset(STACKUP.replace('1.53', '1.531'), manufacturer='Example Fab')
        plan = self.plan()
        self.assertFalse(plan.matches[0].blocking)
        self.assertIn('D1 thickness: 1.531 → 1.53 mm', plan.matches[0].differences)
        with self.assertRaisesRegex(StandardsError, 'Review'):
            save_preset(plan)

    def test_same_layer_count_alone_is_not_a_similar_construction(self):
        self.existing_preset(STACKUP.replace('1.53', '0.9'))
        self.assertEqual(self.plan().matches, ())

    def test_changed_board_or_catalog_prevents_a_stale_save(self):
        plan = self.plan()
        self.board.write_text(self.board.read_text() + '\n')
        before = self.snapshot()
        with self.assertRaisesRegex(StandardsError, 'board changed'):
            save_preset(plan)
        self.assertEqual(before, self.snapshot())
        self.capture = capture_saved_stackup(self.board)
        plan = self.plan()
        self.existing_preset(STACKUP.replace('1.53', '0.9'))
        before = self.snapshot()
        with self.assertRaisesRegex(StandardsError, 'catalog changed'):
            save_preset(plan)
        self.assertEqual(before, self.snapshot())

    def test_capture_requires_a_saved_valid_kicad9_stackup_and_consistent_layers(self):
        original = self.board.read_text()
        for text in (original.replace('20241229', '20260101'), original.replace('B.Cu', 'In1.Cu'),
                     original.replace(STACKUP, ''), original.replace('(layers ', '(layers (4 "In1.Cu" signal) ')):
            with self.subTest(text=text[:50]):
                self.board.write_text(text)
                with self.assertRaises(StandardsError):
                    capture_saved_stackup(self.board)
        self.board.unlink()
        with self.assertRaisesRegex(StandardsError, 'Save the board'):
            capture_saved_stackup(self.board)

    def test_invalid_metadata_and_impedance_references_are_rejected(self):
        guide = {'mode': 'differential', 'impedance_ohms': 100, 'track_width_mm': 0.2,
                 'pair_gap_mm': 0.1, 'layer': 'F.Cu', 'reference_layer': 'B.Cu', 'geometry': 'microstrip'}
        for metadata in ({'manufacturer': ''}, {'name': ''}, {'nominal_thickness_mm': float('inf')},
                         {'outer_copper_oz': -1}, {'source_url': 'file:///local'},
                         {'recommendation_checked': 'yesterday'}, {'status': 'reviewed'},
                         {'impedance_targets': [{**guide, 'pair_gap_mm': 0}]},
                         {'impedance_targets': [{**guide, 'reference_layer': 'In1.Cu'}]},
                         {'impedance_targets': [{**guide, 'reference_layer': 'F.Cu'}]},
                         {'impedance_targets': [{**guide, 'geometry': ''}]}):
            with self.subTest(metadata=metadata), self.assertRaises(StandardsError):
                self.plan(**metadata)

    def test_write_failure_does_not_leave_a_preset_or_temporary_file(self):
        plan = self.plan()
        before = self.snapshot(self.folder)
        with patch('project_manager.stackup_save.os.link', side_effect=OSError('Write denied')):
            with self.assertRaisesRegex(OSError, 'Write denied'):
                save_preset(plan)
        self.assertEqual(before, self.snapshot(self.folder))

    def test_existing_destination_cannot_be_overwritten(self):
        plan = self.plan()
        target = self.folder / plan.relative
        def competing_write(_source, destination):
            Path(destination).write_text('Existing content')
            raise FileExistsError('Already exists')
        with patch('project_manager.stackup_save.os.link', side_effect=competing_write):
            with self.assertRaises(FileExistsError):
                save_preset(plan)
        self.assertEqual(target.read_text(), 'Existing content')
        self.assertFalse(list(self.folder.rglob('*.tmp')))

    def test_failed_post_write_catalog_validation_removes_only_the_new_file(self):
        plan = self.plan()
        before = self.snapshot(self.folder)
        with patch('project_manager.stackup_save.load_catalog', side_effect=[self.catalog, StandardsError('Changed externally')]):
            with self.assertRaisesRegex(StandardsError, 'Changed externally'):
                save_preset(plan)
        self.assertEqual(before, self.snapshot(self.folder))

    def test_preset_paths_cannot_escape_the_catalog(self):
        plan = self.plan(manufacturer='../Mäker / name')
        target = self.folder / plan.relative
        self.assertTrue(target.resolve().is_relative_to(self.folder.resolve()))
        save_preset(plan)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['manufacturer'], '../Mäker / name')
        self.assertIsNotNone(self.plan(manufacturer_stackup_id=None))
        with self.assertRaisesRegex(StandardsError, 'destination'):
            save_preset(replace(plan, relative='../outside.json'))
        with self.assertRaisesRegex(StandardsError, 'Invalid preset ID'):
            prepare_preset(self.capture, self.catalog, self.metadata, preset_id='../outside')
        self.existing_preset(manufacturer=None, manufacturer_stackup_id=None)
        self.assertFalse(self.plan().matches[0].blocking)

    def test_source_and_destination_symlinks_are_rejected(self):
        target = self.base / 'board-link.kicad_pcb'
        try:
            target.symlink_to(self.board)
        except OSError:
            self.skipTest('Symlinks unavailable')
        with self.assertRaisesRegex(StandardsError, 'symbolic'):
            capture_saved_stackup(target)
        plan = self.plan()
        outside = self.base / 'outside'
        outside.mkdir()
        (self.folder / 'stackups/example_fab').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(StandardsError, 'Symbolic'):
            save_preset(plan)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
