"""Synthetic template releases and project customizations; no private fixtures."""
from pathlib import Path
import json
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from project_manager.preferences import save_template
from project_manager.setup import CONFIG, METADATA, WORKFLOW, read_setup, prepare, apply
from project_manager.templates import (MANIFEST, STATE, DOCUMENTS, apply_upgrade, build_manifest,
                                       canonical, compare, load_template, policy, prepare_upgrade,
                                       resolve_template, version_tuple)
from project_manager.yaml_fields import ScalarBlock, SetupError
from tests.fixtures import sample_project

VARIABLES = """kibot:
  version: 1
preflight:
  set_text_variables:
    # Changelog examples
    # - variable: '@RELEASE_TITLE_VAR@1.0.0'
    #   command: '@GET_TITLE_CMD@ 1.0.0'
    # - variable: '@RELEASE_BODY_VAR@1.0.0'
    #   command: '@GET_BODY_CMD@ 1.0.0'
    - variable: '@RELEASE_TITLE_VAR@UNRELEASED'
      command: '@GET_TITLE_CMD@ Unreleased'
    - variable: '@RELEASE_BODY_VAR@UNRELEASED'
      command: '@GET_BODY_CMD@ Unreleased'
    - variable: 'PROJECT_NAME'
      text: '@PROJECT_NAME@'
...
definitions:
  GET_TITLE_CMD: python3 scripts/changelog.py --title
  GET_BODY_CMD: python3 scripts/changelog.py --body
"""
HISTORY = """    - variable: '@RELEASE_TITLE_VAR@0.5.0'
      command: '@GET_TITLE_CMD@ 0.5.0'
    - variable: '@RELEASE_BODY_VAR@0.5.0'
      command: '@GET_BODY_CMD@ 0.5.0'
"""
VAR_PATH = 'kibot_yaml/kibot_pre_set_text_variables.yaml'


def release(root: Path, version='1.0.0'):
    (root / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_bytes(build_manifest(root, 'example.template', version))
    return load_template(root)


def template_folder(root: Path):
    sample_project(root)
    resources = root / 'kibot_resources'
    (resources / 'fonts').mkdir(parents=True)
    (resources / 'fonts/font.ttf').write_bytes(b'\x00\xffFONT')
    (resources / 'colors').mkdir()
    (resources / 'colors/theme.json').write_text('{"theme": "example"}\n')
    (resources / 'scripts').mkdir()
    (resources / 'scripts/helper.py').write_text('print("example")\n')
    (resources / 'templates').mkdir()
    for path in DOCUMENTS:
        (root / path).write_text('Company introduction\nBoard-specific middle\nCompany ending\n')
    (root / VAR_PATH).write_text(VARIABLES)
    return release(root)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.template = template_folder(self.base / 'template')
        self.project = self.base / 'project'
        shutil.copytree(self.template.root, self.project)
        self.storage = self.base / 'user-settings'

    def inspect(self):
        return compare(self.project, self.template)

    def adopt(self):
        comparison = self.inspect()
        plan = prepare_upgrade(comparison, {i.path: 'keep' if i.suggested == 'review' else i.suggested
                                            for i in comparison.items})
        apply_upgrade(plan, self.storage)
        return plan

    def test_new_project_checks_all_files_without_plugin_creation(self):
        comparison = self.inspect()
        self.assertEqual(comparison.version, '1.0.0')
        self.assertEqual(comparison.colour, 'green')
        self.assertFalse(comparison.problems)
        self.assertFalse((self.project / STATE).exists())

    def test_metadata_quoting_line_endings_history_and_status_are_preserved(self):
        original = (self.project / CONFIG).read_bytes()
        values = {key: f'Project {key}' for key in METADATA}
        updated = ScalarBlock(original, 'definitions', METADATA).replace(values)
        (self.project / CONFIG).write_bytes(updated.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        workflow = (self.project / WORKFLOW).read_bytes().replace(b'DRAFT', b'CHECKED')
        (self.project / WORKFLOW).write_bytes(workflow)
        (self.project / VAR_PATH).write_text(VARIABLES.replace("    - variable: '@RELEASE_TITLE_VAR@UNRELEASED'", HISTORY + "    - variable: '@RELEASE_TITLE_VAR@UNRELEASED'"))
        self.assertFalse(self.inspect().problems)
        self.adopt()
        source_config = self.template.root / CONFIG
        source_config.write_bytes(source_config.read_bytes().replace(b'type: report', b'type: report_updated'))
        source_vars = self.template.root / VAR_PATH
        source_vars.write_text(VARIABLES.replace('--title', '--title-only'))
        self.template = release(self.template.root, '1.1.0')
        self.adopt()
        self.assertEqual(read_setup(self.project).metadata, values)
        self.assertEqual(read_setup(self.project).status, 'CHECKED')
        self.assertIn(b'\r\n', (self.project / CONFIG).read_bytes())
        self.assertIn(HISTORY, (self.project / VAR_PATH).read_text())
        self.assertIn('--title-only', (self.project / VAR_PATH).read_text())
        self.assertFalse(self.inspect().problems)

    def test_version_match_does_not_hide_missing_font_or_changed_theme(self):
        (self.project / 'kibot_resources/fonts/font.ttf').unlink()
        (self.project / 'kibot_resources/colors/theme.json').write_text('{}')
        comparison = self.inspect()
        self.assertEqual(comparison.colour, 'red')
        self.assertEqual(len(comparison.problems), 2)
        self.assertIn('Missing required files', comparison.summary)

    def test_reviewer_preserved_when_upgrading_a_legacy_template(self):
        config = self.template.root / CONFIG
        config.write_bytes(config.read_bytes().replace(b"  REVIEWER: ''\n", b''))
        self.template = release(self.template.root, '1.0.0')
        project_config = self.project / CONFIG
        project_config.write_bytes(config.read_bytes())
        (self.project / MANIFEST).write_bytes(self.template.manifest)
        self.adopt()
        config.write_bytes(config.read_bytes().replace(b'  DESIGNER: Author\n',
                           b"  DESIGNER: Author\n  REVIEWER: ''\n"))
        self.template = release(self.template.root, '1.1.0')
        self.adopt()
        self.assertEqual(read_setup(self.project).metadata['REVIEWER'], '')
        snapshot = read_setup(self.project)
        apply(prepare(snapshot, {**snapshot.metadata, 'REVIEWER': 'Review Engineer'}, snapshot.status), self.storage)
        self.assertFalse(self.inspect().problems)
        config.write_bytes(config.read_bytes().replace(b'type: report', b'type: report_updated'))
        self.template = release(self.template.root, '1.2.0')
        self.adopt()
        self.assertEqual(read_setup(self.project).metadata['REVIEWER'], 'Review Engineer')

    def test_restored_files_and_repeat_apply(self):
        (self.project / WORKFLOW).unlink()
        (self.project / 'kibot_resources/fonts/font.ttf').unlink()
        plan = self.adopt()
        self.assertTrue(plan.changes)
        self.assertEqual(self.inspect().colour, 'green')
        self.assertFalse(self.adopt().changes)

    def test_document_edits_preserved_and_incoming_changes_reviewed(self):
        self.adopt()
        path = sorted(DOCUMENTS)[0]
        project_document = self.project / path
        project_document.write_text('My project documentation\n')
        self.assertFalse(self.inspect().problems)
        (self.template.root / path).write_text('New company guidance\n')
        self.template = release(self.template.root, '1.1.0')
        comparison = self.inspect()
        item = next(i for i in comparison.items if i.path == path)
        self.assertEqual(item.suggested, 'review')
        self.assertIn('Review project content', item.status)
        self.assertIn('Company introduction', item.base['content'])
        self.adopt()  # Explicit review accepts the preserved content for this release.
        self.assertEqual(project_document.read_text(), 'My project documentation\n')
        self.assertFalse(self.inspect().problems)

    def test_document_conflict_requires_an_explicit_decision(self):
        path = sorted(DOCUMENTS)[0]
        (self.project / path).write_text('Custom project note\n')
        comparison = self.inspect()
        with self.assertRaisesRegex(SetupError, 'Choose whether to keep'):
            prepare_upgrade(comparison, {i.path: i.suggested for i in comparison.items})

    def test_manual_document_merge(self):
        path = sorted(DOCUMENTS)[0]
        comparison = self.inspect()
        choices = {i.path: i.suggested for i in comparison.items}
        choices[path] = 'edit'
        plan = prepare_upgrade(comparison, choices, {path: b'Merged project and company text\n'})
        apply_upgrade(plan, self.storage)
        self.assertEqual((self.project / path).read_text(), 'Merged project and company text\n')
        self.assertFalse(self.inspect().problems)

    def test_nonoverlapping_document_changes_propose_a_three_way_merge(self):
        self.adopt()
        path = sorted(DOCUMENTS)[0]
        (self.project / path).write_text('Company introduction\nMy board-specific middle\nCompany ending\n')
        (self.template.root / path).write_text('New company introduction\nBoard-specific middle\nCompany ending\n')
        self.template = release(self.template.root, '1.1.0')
        item = next(i for i in self.inspect().items if i.path == path)
        self.assertEqual(item.suggested, 'edit')
        self.adopt()
        self.assertEqual((self.project / path).read_text(),
                         'New company introduction\nMy board-specific middle\nCompany ending\n')

    def test_legacy_project_stays_unknown_until_reviewed(self):
        (self.project / MANIFEST).unlink()
        self.assertIsNone(self.inspect().version)
        self.assertEqual(self.inspect().colour, 'neutral')
        self.adopt()
        self.assertEqual(self.inspect().version, '1.0.0')
        self.assertEqual(self.inspect().colour, 'green')

    def test_unexpected_yaml_and_workflow_changes_are_detected(self):
        (self.project / CONFIG).write_bytes((self.project / CONFIG).read_bytes().replace(b'UNRELATED:', b'NEW_SETTING:'))
        (self.project / WORKFLOW).write_bytes((self.project / WORKFLOW).read_bytes().replace(b'[dev]', b'[main]'))
        comparison = self.inspect()
        self.assertEqual(comparison.colour, 'amber')
        self.assertEqual(len(comparison.problems), 2)
        self.adopt()  # Keeping a company-file exception never makes it green.
        self.assertEqual(self.inspect().colour, 'amber')

    def test_release_commands_and_unreleased_are_not_blanket_ignored(self):
        data = VARIABLES.replace("    - variable: '@RELEASE_TITLE_VAR@UNRELEASED'", HISTORY + "    - variable: '@RELEASE_TITLE_VAR@UNRELEASED'")
        (self.project / VAR_PATH).write_text(data.replace('@GET_TITLE_CMD@ 0.5.0', 'unexpected-command 0.5.0'))
        self.assertEqual(len(self.inspect().problems), 1)
        (self.project / VAR_PATH).write_text(VARIABLES.replace('@GET_BODY_CMD@ Unreleased', '@GET_BODY_CMD@ wrong'))
        self.assertEqual(len(self.inspect().problems), 1)

    def test_uncommenting_first_release_variables_is_an_allowed_project_change(self):
        active = VARIABLES.replace('    # - variable:', '    - variable:').replace(
            '    #   command:', '      command:')
        (self.project / VAR_PATH).write_text(active)
        self.assertFalse(self.inspect().problems)
        self.adopt()
        self.assertEqual((self.project / VAR_PATH).read_text(), active)

    def test_uncommenting_with_an_extra_space_is_not_silently_accepted(self):
        invalid = VARIABLES.replace('    # - variable:', '     - variable:').replace(
            '    #   command:', '       command:')
        (self.project / VAR_PATH).write_text(invalid)
        comparison = self.inspect()
        item = next(item for item in comparison.items if item.path == VAR_PATH)
        self.assertIn('Local modifications', item.status)
        self.assertEqual(comparison.colour, 'amber')

    def test_workflow_block_scalar_contents_are_never_ignored_as_yaml_comments(self):
        path = self.template.root / WORKFLOW
        path.write_text(path.read_text() + '\n  generated:\n    run: |\n      cat << EOF\n      # content in a generated file\n      EOF\n')
        self.template = release(self.template.root, '1.1.0')
        current = path.read_bytes()
        (self.project / WORKFLOW).write_bytes(current.replace(b'# content in a generated file', b'# different generated file'))
        self.assertEqual(len(self.inspect().problems), 1)

    def test_published_version_cannot_be_reused_for_different_contents(self):
        target = self.template.root / 'kibot_resources/scripts/helper.py'
        target.write_text('changed template source\n')
        self.template = release(self.template.root)
        with self.assertRaisesRegex(SetupError, 'reuses a recorded version'):
            self.inspect()

    def test_project_and_source_edits_during_review_abort_before_writing(self):
        plan = prepare_upgrade(self.inspect(), {i.path: i.suggested for i in self.inspect().items})
        target = self.project / 'kibot_resources/scripts/helper.py'
        target.write_text('external change\n')
        with self.assertRaisesRegex(SetupError, 'changed during review'):
            apply_upgrade(plan, self.storage)
        self.assertFalse((self.project / STATE).exists())
        self.assertEqual(target.read_text(), 'external change\n')
        comparison = self.inspect()
        plan = prepare_upgrade(comparison, {i.path: 'keep' if i.suggested == 'review' else i.suggested
                                            for i in comparison.items})
        (self.template.root / 'kibot_resources/scripts/helper.py').write_text('changed source\n')
        with self.assertRaisesRegex(SetupError, 'Installed template modified'):
            apply_upgrade(plan, self.storage)

    def test_failed_multifile_apply_rolls_back_including_provenance(self):
        (self.project / WORKFLOW).unlink()
        comparison = self.inspect()
        plan = prepare_upgrade(comparison, {i.path: i.suggested for i in comparison.items})
        from project_manager import setup
        original = setup._replace
        def fail(path, data, mode):
            if path.name == 'project.json':
                raise OSError('synthetic disk failure')
            return original(path, data, mode)
        with patch('project_manager.setup._replace', side_effect=fail):
            with self.assertRaises(SetupError):
                apply_upgrade(plan, self.storage)
        self.assertFalse((self.project / WORKFLOW).exists())
        self.assertFalse((self.project / STATE).exists())
        self.assertEqual((self.project / MANIFEST).read_bytes(), comparison.manifest_before)

    def test_manifest_must_match_complete_source_inventory_and_contents(self):
        path = self.template.root / 'kibot_resources/fonts/extra.ttf'
        path.write_bytes(b'extra')
        with self.assertRaisesRegex(SetupError, 'Installed template files were added, removed or renamed'):
            load_template(self.template.root)
        path.unlink()
        path = self.template.root / 'kibot_resources/fonts/font.ttf'
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(SetupError, 'Installed template modified'):
            load_template(self.template.root)

    def test_missing_workflow_is_added_by_template_review_with_source_branch_policy(self):
        (self.project / WORKFLOW).unlink()
        source = self.template.root / WORKFLOW
        source.write_bytes(source.read_bytes().replace(b'[dev]', b'[main, dev]'))
        self.template = release(self.template.root, '1.1.0')
        comparison = self.inspect()
        workflow = next(item for item in comparison.items if item.path == WORKFLOW)
        self.assertEqual(workflow.suggested, 'use')
        self.adopt()
        self.assertIn(b'[main, dev]', (self.project / WORKFLOW).read_bytes())
        self.assertEqual((self.project / WORKFLOW).read_bytes(), source.read_bytes())

    def test_workflow_created_during_template_review_is_not_overwritten(self):
        (self.project / WORKFLOW).unlink()
        comparison = self.inspect()
        plan = prepare_upgrade(comparison, {item.path: item.suggested for item in comparison.items})
        (self.project / WORKFLOW).write_bytes(b'external workflow')
        with self.assertRaisesRegex(SetupError, 'changed during review'):
            apply_upgrade(plan, self.storage)
        self.assertEqual((self.project / WORKFLOW).read_bytes(), b'external workflow')
        self.assertFalse((self.project / STATE).exists())

    def test_source_newlines_do_not_invalidate_manifest(self):
        for path in self.template.files:
            if policy(path) != 'binary':
                target = self.template.root / path
                target.write_bytes(target.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        load_template(self.template.root)

    def test_no_arbitrary_paths_symlinks_or_template_switches(self):
        for path in ('../outside', '/tmp/outside', 'kibot_yaml/../../outside.yaml', 'kibot_yaml\\escape.yaml',
                     'sample.kicad_pcb', 'kibot_resources/project_manager/template.json'):
            with self.assertRaises(SetupError):
                policy(path)
        manifest = json.loads((self.project / MANIFEST).read_text())
        manifest['template_id'] = 'different.template'
        (self.project / MANIFEST).write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SetupError, 'different template'):
            self.inspect()

    def test_downgrades_and_unreviewed_files_are_rejected(self):
        self.adopt()
        self.template = release(self.template.root, '0.9.0')
        comparison = self.inspect()
        self.assertIn('Installed template is older', comparison.summary)
        with self.assertRaisesRegex(SetupError, 'downgrades'):
            prepare_upgrade(comparison, {i.path: i.suggested for i in comparison.items})
        with self.assertRaisesRegex(SetupError, 'Review every'):
            prepare_upgrade(comparison, {})
        self.assertGreater(version_tuple('1.10.0'), version_tuple('1.9.9'))

    def test_additional_project_files_are_preserved(self):
        extra = self.project / 'kibot_resources/fonts/custom.ttf'
        extra.write_bytes(b'custom')
        self.assertIn('kibot_resources/fonts/custom.ttf', self.inspect().extras)
        self.adopt()
        self.assertEqual(extra.read_bytes(), b'custom')

    def test_source_paths_are_local_and_resolved_by_identity(self):
        self.assertEqual(resolve_template(self.project, self.storage, self.base), self.template.root)
        save_template(str(self.template.root), self.storage, self.template.identity)
        self.assertEqual(resolve_template(self.project, self.storage, self.base / 'absent'), self.template.root)
        self.adopt()
        self.assertNotIn(str(self.template.root), (self.project / STATE).read_text())

    def test_duplicate_json_and_corrupt_record_do_not_get_overwritten(self):
        state = self.project / STATE
        state.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(SetupError, 'duplicate key'):
            self.inspect()

    @unittest.skipIf(__import__('sys').platform == 'win32', 'symlink permission differs on Windows')
    def test_source_discovery_and_saved_paths_resolve_directory_aliases(self):
        alias = self.base / 'workstation-alias'
        alias.symlink_to(self.base, target_is_directory=True)
        self.assertEqual(resolve_template(alias / 'project', self.storage, alias), self.template.root)
        save_template(str(alias / 'template'), self.storage, self.template.identity)
        self.assertEqual(resolve_template(self.project, self.storage, self.base), self.template.root)

    @unittest.skipIf(__import__('sys').platform == 'win32', 'symlink permission differs on Windows')
    def test_symlink_inside_managed_folder_rejected(self):
        target = self.project / 'kibot_resources/fonts/font.ttf'
        target.unlink()
        target.symlink_to(self.template.root / 'kibot_resources/fonts/font.ttf')
        with self.assertRaisesRegex(SetupError, 'symbolic link'):
            self.inspect()


if __name__ == '__main__':
    unittest.main()
