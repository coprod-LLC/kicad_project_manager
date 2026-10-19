"""Regression coverage for 10-saved boards and explicit standards compatibility."""
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from project_manager.context import detect_context
from project_manager.setup import apply, prepare, read_setup
from project_manager.preferences import kicad_user_template_directory
from project_manager.stackup_save import capture_saved_stackup, prepare_preset, save_preset
from project_manager.standards import StandardsError, load_catalog
from project_manager.standards_apply import _areas_and_nets, apply_standards, plan_standards
from tests.fixtures import sample_project
from tests.standards_fixtures import catalog_folder, saved_board
from tests.test_templates import template_folder, release
from project_manager.templates import apply_upgrade, compare, prepare_upgrade


class KiCad10Tests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = catalog_folder(self.root / 'standards')
        self.project, self.board = saved_board(self.root / 'project')
        text = self.board.read_text().replace('20241229', '20260206')
        # New board format: net names reside on connected objects, not root netcodes.
        text = text.replace('(net 1 "FRAME")', '(segment (net "FRAME"))')
        self.board.write_text(text)

    def qualify_catalog(self):
        for path in [self.folder / 'catalog.json', self.folder / 'stackups/two_layer.json']:
            data = json.loads(path.read_text())
            data['compatible_kicad_majors'] = [9, 10]
            path.write_text(json.dumps(data))
        return load_catalog(self.folder)

    def test_ipc_uses_the_launching_kicad10_document(self):
        document = NS(project=NS(path=str(self.project.parent), name='sample'), board_filename='sample.kicad_pcb')
        client = lambda **kw: NS(get_version=lambda: NS(major=10, full_version='10.0.7'),
                                 get_open_documents=lambda kind: [document])
        result = detect_context('ipc:///test', client_factory=client, environ={})
        self.assertEqual(result.kicad_version, '10.0.7')
        self.assertEqual(result.board_file, str(self.board))

    def test_setup_preserves_kicad10_workflow_selection(self):
        folder = sample_project(self.root / 'setup')
        workflow = folder / '.github/workflows/ci.yaml'
        workflow.write_bytes(workflow.read_bytes().replace(b'kicad_version: 9', b'kicad_version: 10'))
        snapshot = read_setup(folder)
        apply(prepare(snapshot, {**snapshot.metadata, 'PROJECT_NAME': 'KiCad 10 board'}, 'CHECKED'),
              self.root / 'settings')
        self.assertIn(b'kicad_version: 10', workflow.read_bytes())
        self.assertEqual(read_setup(folder).metadata['PROJECT_NAME'], 'KiCad 10 board')

    def test_template_default_follows_connected_version_and_document_override(self):
        self.assertEqual(kicad_user_template_directory({'XDG_DATA_HOME': '/data'}, 'linux', 10),
                         Path('/data/kicad/10.0/template'))
        self.assertEqual(kicad_user_template_directory({'KICAD_DOCUMENTS_HOME': '/trial/docs'}, 'linux', 10),
                         Path('/trial/docs/kicad/10.0/template'))

    def test_kicad10_settings_and_instance_lock_have_a_separate_directory(self):
        from project_manager.preferences import configure_runtime, data_directory
        from unittest.mock import patch
        self.addCleanup(configure_runtime, 9)
        with patch.dict('os.environ', {'XDG_DATA_HOME': str(self.root)}), \
             patch('sys.platform', 'linux'):
            configure_runtime(9)
            legacy = data_directory()
            configure_runtime(10)
            self.assertEqual(data_directory(), legacy / '10.0')

    def test_template_upgrade_preserves_project_kicad_version_without_repeated_differences(self):
        template = template_folder(self.root / 'template')
        project = self.root / 'upgrade-project'
        shutil.copytree(template.root, project)
        comparison = compare(project, template)
        apply_upgrade(prepare_upgrade(comparison, {item.path: 'use' for item in comparison.items}),
                      self.root / 'settings')
        workflow = template.root / '.github/workflows/ci.yaml'
        workflow.write_bytes(workflow.read_bytes().replace(b'kicad_version: 9', b'kicad_version: 10'))
        template = release(template.root, '1.1.0')
        comparison = compare(project, template)
        apply_upgrade(prepare_upgrade(comparison, {item.path: 'use' for item in comparison.items}),
                      self.root / 'settings')
        self.assertIn(b"kicad_version: '9'", (project / '.github/workflows/ci.yaml').read_bytes())
        workflow_item = next(item for item in compare(project, template).items
                             if item.path == '.github/workflows/ci.yaml')
        self.assertEqual(workflow_item.status, 'Matches')

    def test_unqualified_presets_cannot_edit_newer_boards(self):
        catalog = load_catalog(self.folder)
        before = self.board.read_bytes()
        with self.assertRaisesRegex(StandardsError, 'not been qualified for KiCad 10'):
            plan_standards(self.project, self.board, None, catalog.stackups[0], catalog)
        self.assertEqual(self.board.read_bytes(), before)

    def test_stackup_apply_preserves_new_format_nets_and_unrelated_nodes(self):
        preset = self.folder / 'stackups/two_layer.json'
        preset.write_text(preset.read_text().replace('1.53', '1.4'))
        catalog = self.qualify_catalog()
        extra = '(property "port-test" "Preserve \\"quoted\\" text")'
        self.board.write_text(self.board.read_text().rstrip()[:-1] + extra + ')\n')
        plan = plan_standards(self.project, self.board, None, catalog.stackups[0], catalog)
        apply_standards(plan, self.root / 'settings')
        text = self.board.read_text()
        self.assertIn('(version 20260206)', text)
        self.assertIn('(segment (net "FRAME"))', text)
        self.assertIn(extra, text)
        self.assertAlmostEqual(capture_saved_stackup(self.board).physical.total_mm, 1.47)

    def test_new_presets_record_the_captured_board_major(self):
        self.qualify_catalog()
        (self.folder / 'stackups/two_layer.json').unlink()
        catalog = load_catalog(self.folder)
        capture = capture_saved_stackup(self.board)
        self.assertEqual(capture.kicad_major, 10)
        plan = prepare_preset(capture, catalog, {'name': 'Port construction', 'manufacturer': 'Test Fab'},
                              preset_id='test_10')
        saved = save_preset(plan)
        self.assertEqual(json.loads(plan.text)['kicad_major'], 10)
        self.assertEqual(saved.stackups[0].details['compatible_kicad_majors'], [10])

    def test_unknown_future_format_is_rejected_before_writes(self):
        self.board.write_text(self.board.read_text().replace('20260206', '20270301'))
        catalog = self.qualify_catalog()
        with self.assertRaises(StandardsError):
            plan_standards(self.project, self.board, None, catalog.stackups[0], catalog)
        with self.assertRaises(StandardsError):
            capture_saved_stackup(self.board)

    def test_net_discovery_handles_both_formats_and_escaped_names(self):
        text = '(kicad_pcb (net 1 "Old") (footprint "X" (pad "1" (net "New\\\"net"))))'
        self.assertEqual(_areas_and_nets(text)[1], {'Old', 'New"net'})


if __name__ == '__main__':
    unittest.main()
