from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest
from unittest.mock import patch

from project_manager import setup
from project_manager.preferences import load_template, save_template
from project_manager.yaml_fields import ScalarBlock, SetupError
from tests.fixtures import CONFIG_TEXT, WORKFLOW_TEXT, sample_project


class FieldsTests(unittest.TestCase):
    def test_noop_preserves_every_byte_including_bom_crlf_comments_and_macros(self):
        data = ('\ufeff' + CONFIG_TEXT).replace('\n', '\r\n').encode()
        block = ScalarBlock(data, 'definitions', setup.METADATA)
        self.assertEqual(block.replace(block.values), data)
        changed = block.replace({'PROJECT_NAME': 'Café controller'})
        self.assertEqual(changed, data.replace(b'PROJECT_NAME: Project Name', "PROJECT_NAME: 'Café controller'".encode()))

    def test_quoted_scalars_roundtrip_and_comment_hash_in_value(self):
        data = b"env:\n  field: 'Alice''s #1' # preserved\n"
        block = ScalarBlock(data, 'env', ('field',))
        self.assertEqual(block.values['field'], "Alice's #1")
        changed = block.replace({'field': 'other "value"'})
        self.assertEqual(ScalarBlock(changed, 'env', ('field',)).values['field'], 'other "value"')
        self.assertTrue(changed.endswith(b' # preserved\n'))

    def test_unsupported_structures_and_duplicates_are_rejected(self):
        for body in ['  field: |\n    value\n', '  field: a\n  field: b\n',
                     '  field: a\n    continuation\n', '  field: &anchor value\n',
                     '  field: {a: b}\n', '  field:\n', '  field: "bad\\q"\n']:
            with self.subTest(body=body), self.assertRaises(SetupError):
                ScalarBlock(('env:\n' + body).encode(), 'env', ('field',))
        with self.assertRaises(SetupError):
            ScalarBlock(b'env:\n  field: a\nenv:\n  field: b\n', 'env', ('field',))


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = sample_project(self.base / 'project')
        self.store = self.base / 'local-settings'

    def values(self, snapshot):
        return {**snapshot.metadata, 'PROJECT_NAME': 'Example controller', 'BOARD_NAME': 'Control board',
                'DESIGNER': 'Alice Martin; Zoë Chen', 'GIT_URL': 'https://example.org/team/board'}

    def test_preview_does_not_write_and_apply_reloads_with_no_second_change(self):
        before = setup.read_setup(self.root)
        board = (self.root / 'sample.kicad_pcb').read_bytes()
        plan = setup.prepare(before, self.values(before), 'PRELIMINARY')
        self.assertIn("+  PROJECT_NAME: 'Example controller'", plan.diff())
        self.assertEqual(setup.read_setup(self.root), before)
        backup = setup.apply(plan, self.store)
        self.assertEqual((backup / setup.CONFIG).read_bytes(), before.config)
        self.assertEqual((backup / setup.WORKFLOW).read_bytes(), before.workflow)
        self.assertEqual((self.root / 'sample.kicad_pcb').read_bytes(), board)
        self.assertEqual((self.root / 'sample.kicad_pro').read_text(), '{}\n')
        after = setup.read_setup(self.root)
        self.assertEqual(after.metadata, self.values(before))
        self.assertEqual(after.status, 'PRELIMINARY')
        self.assertIn(b"branches: [dev]", after.workflow)
        self.assertIn(b"'@KEEP_THIS@'", after.config)
        self.assertEqual(setup.prepare(after, after.metadata, after.status).changes, ())
        self.assertIsNone(setup.apply(setup.prepare(after, after.metadata, after.status), self.store))

    def test_restore_missing_workflow_preserves_selected_template_branch_policy(self):
        (self.root / setup.WORKFLOW).unlink()
        template = sample_project(self.base / 'template')
        original = (template / setup.WORKFLOW).read_bytes().replace(b'[dev]', b'[main, dev]')
        (template / setup.WORKFLOW).write_bytes(original)
        snapshot = setup.read_setup(self.root)
        plan = setup.prepare(snapshot, snapshot.metadata, 'CHECKED', template, True)
        self.assertIsNone(plan.changes[0].before)
        self.assertIn('/dev/null', plan.diff())
        backup = setup.apply(plan, self.store)
        self.assertIn(b'[main, dev]', (self.root / setup.WORKFLOW).read_bytes())
        self.assertEqual((template / setup.WORKFLOW).read_bytes(), original)
        self.assertFalse(json.loads((backup / 'manifest.json').read_text())['files'][0]['existed'])

    def test_existing_workflow_is_never_replaced_by_template(self):
        template = sample_project(self.base / 'template')
        (template / setup.WORKFLOW).write_bytes(b'not a supported workflow')
        snapshot = setup.read_setup(self.root)
        self.assertEqual(setup.prepare(snapshot, snapshot.metadata, snapshot.status, template, True).changes, ())

    def test_metadata_only_when_missing_workflow_restoration_not_selected(self):
        (self.root / setup.WORKFLOW).unlink()
        snapshot = setup.read_setup(self.root)
        plan = setup.prepare(snapshot, self.values(snapshot), 'DRAFT')
        setup.apply(plan, self.store)
        self.assertFalse((self.root / setup.WORKFLOW).exists())

    def test_external_change_to_either_file_blocks_all_writes(self):
        for relative in (setup.CONFIG, setup.WORKFLOW):
            with self.subTest(relative=relative):
                snapshot = setup.read_setup(self.root)
                plan = setup.prepare(snapshot, self.values(snapshot), 'CHECKED')
                path = self.root / relative
                original = path.read_bytes()
                path.write_bytes(original + b'# someone else edited\n')
                expected = {name: (self.root / name).read_bytes() for name in (setup.CONFIG, setup.WORKFLOW)}
                with self.assertRaisesRegex(SetupError, 'changed since'):
                    setup.apply(plan, self.store)
                self.assertEqual(expected, {name: (self.root / name).read_bytes() for name in expected})
                path.write_bytes(original)

    def test_workflow_created_after_preview_is_not_overwritten(self):
        (self.root / setup.WORKFLOW).unlink()
        snapshot = setup.read_setup(self.root)
        template = sample_project(self.base / 'template')
        plan = setup.prepare(snapshot, self.values(snapshot), 'CHECKED', template, True)
        (self.root / setup.WORKFLOW).write_bytes(b'new work')
        with self.assertRaises(SetupError):
            setup.apply(plan, self.store)
        self.assertEqual((self.root / setup.CONFIG).read_bytes(), snapshot.config)

    def test_failure_on_second_file_rolls_back_first_exactly(self):
        snapshot = setup.read_setup(self.root)
        plan = setup.prepare(snapshot, self.values(snapshot), 'CHECKED')
        replace = setup._replace
        calls = []
        def fail_second(path, data, mode):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('simulated disk failure')
            replace(path, data, mode)
        with patch.object(setup, '_replace', fail_second), self.assertRaisesRegex(SetupError, 'original files were retained'):
            setup.apply(plan, self.store)
        self.assertEqual(setup.read_setup(self.root), snapshot)
        self.assertEqual(len(calls), 3)
        self.assertFalse(list(self.store.rglob('apply.lock')))

    def test_conflict_during_apply_never_overwrites_external_work_on_rollback(self):
        snapshot = setup.read_setup(self.root)
        plan = setup.prepare(snapshot, self.values(snapshot), 'CHECKED')
        replace = setup._replace
        def conflict(path, data, mode):
            if path.name == 'ci.yaml':
                (self.root / setup.CONFIG).write_bytes(b'external work')
                raise OSError('simulated conflict')
            replace(path, data, mode)
        with patch.object(setup, '_replace', conflict), self.assertRaisesRegex(SetupError, 'Restore'):
            setup.apply(plan, self.store)
        self.assertEqual((self.root / setup.CONFIG).read_bytes(), b'external work')

    def test_symlink_escape_is_rejected(self):
        target = self.root / setup.CONFIG
        external = self.base / 'external.yaml'
        external.write_bytes(target.read_bytes())
        target.unlink()
        try:
            target.symlink_to(external)
        except OSError:
            self.skipTest('This OS/account cannot create symlinks')
        with self.assertRaisesRegex(SetupError, 'symbolic link'):
            setup.read_setup(self.root)

    def test_read_only_git_suggestions_respect_existing_metadata(self):
        snapshot = setup.read_setup(self.root)
        with patch.object(setup, 'git_value', side_effect=['Zoë Chen', 'git@example.org:team/board.git']):
            values = setup.suggested_metadata(snapshot, 'my_project', 'my_board')
        self.assertEqual(values['PROJECT_NAME'], 'my_project')
        self.assertEqual(values['DESIGNER'], 'Zoë Chen')
        self.assertEqual(values['GIT_URL'], 'https://example.org/team/board')
        self.assertEqual(values['COMPANY'], 'Example Company')
        plan = setup.prepare(snapshot, values, 'DRAFT')
        setup.apply(plan, self.store)
        with patch.object(setup, 'git_value', return_value='someone else'):
            self.assertEqual(setup.suggested_metadata(setup.read_setup(self.root), 'other', 'other'), values)

    def test_remote_credentials_are_never_suggested(self):
        self.assertEqual(setup.web_url('https://name:secret@example.org/team/board.git?token=secret#secret'), 'https://example.org/team/board')
        self.assertEqual(setup.web_url('ssh://git@example.org:2222/team/board.git'), 'https://example.org/team/board')
        self.assertEqual(setup.web_url('/private/local/repo'), '')

    def test_unsafe_template_substitutions_rejected_before_any_write(self):
        snapshot = setup.read_setup(self.root)
        for value in ["O'Brien", 'Project: rev 2', 'Project # comment', '@INJECT@', 'yes', '42', '[array]', '- item', '.nan', '2026-10-16']:
            with self.subTest(value=value), self.assertRaises(SetupError):
                setup.prepare(snapshot, {**snapshot.metadata, 'DESIGNER': value}, 'DRAFT')
        self.assertEqual(setup.read_setup(self.root), snapshot)

    def test_released_and_custom_status_are_preserved_but_not_new_choices(self):
        snapshot = setup.read_setup(self.root)
        with self.assertRaisesRegex(SetupError, 'version tags'):
            setup.prepare(snapshot, snapshot.metadata, 'RELEASED')
        path = self.root / setup.WORKFLOW
        path.write_bytes(snapshot.workflow.replace(b'DRAFT', b'CUSTOM'))
        custom = setup.read_setup(self.root)
        self.assertEqual(setup.prepare(custom, custom.metadata, 'CUSTOM').changes, ())
        self.assertTrue(setup.prepare(custom, custom.metadata, 'CHECKED').changes)

    def test_wrong_workflow_config_or_kicad_version_is_rejected(self):
        for old, new in [(b'kicad_version: 9', b'kicad_version: 10'),
                         (b'kibot_yaml/kibot_main.yaml', b'other.yaml')]:
            with self.subTest(new=new), self.assertRaises(SetupError):
                setup.workflow_block(WORKFLOW_TEXT.encode().replace(old, new))

    def test_local_template_preference_roundtrip(self):
        self.assertEqual(load_template(self.store), '')
        save_template(str(self.root), self.store)
        self.assertEqual(load_template(self.store), str(self.root))
        self.assertFalse((self.root / 'settings.json').exists())


if __name__ == '__main__':
    unittest.main()
