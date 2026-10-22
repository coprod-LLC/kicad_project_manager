"""Shared ignore-rule upgrades and explicit README resets preserve project data."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from project_manager.templates import (ACCEPTED, IGNORE_SHARED, IGNORE_USER, LICENSE_FILE,
                                       README_SOURCE, STATE, apply_upgrade, compare, load_template,
                                       prepare_upgrade)
from project_manager.yaml_fields import SetupError
from tests.test_templates import release, template_folder


def ignore_file(patterns='*.log\n', custom='# Local rules\n'):
    return f'{IGNORE_SHARED}\n{patterns}\n{IGNORE_USER}\n{custom}'


class ProjectContentTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.template = template_folder(self.base / 'template')
        self.project = self.base / 'project'
        shutil.copytree(self.template.root, self.project)
        self.storage = self.base / 'backups'

    def publish_ignore(self, value=None, version='1.1.0'):
        (self.template.root / '.gitignore').write_text(value or ignore_file())
        self.template = release(self.template.root, version)

    def inspect(self):
        return compare(self.project, self.template)

    def apply(self, choices):
        return apply_upgrade(prepare_upgrade(self.inspect(), choices), self.storage)

    def test_legacy_template_ignore_is_not_silently_added_to_its_manifest(self):
        (self.template.root / '.gitignore').write_text('*.log\n')
        self.assertNotIn('.gitignore', load_template(self.template.root).files)
        self.assertNotIn('LICENSE', self.template.files)

    def test_unmarked_project_rules_survive_adoption_and_are_backed_up(self):
        legacy = b'*.bak\r\n# Existing exceptions\r\n!keep.bak\r\ncustom/\r\n'
        (self.project / '.gitignore').write_bytes(legacy)
        self.publish_ignore()
        backup = self.apply({'.gitignore': 'use'})
        expected = ignore_file(custom=legacy.decode().replace('\r\n', '\n')).replace('\n', '\r\n')
        self.assertEqual((self.project / '.gitignore').read_bytes(), expected.encode())
        self.assertEqual((backup / '.gitignore').read_bytes(), legacy)
        self.assertFalse(self.inspect().problems)

    def test_shared_upgrade_preserves_user_rules_and_git_negation(self):
        self.publish_ignore()
        self.apply({'.gitignore': 'use'})
        target = self.project / '.gitignore'
        custom = '# Project exceptions\n!keep.log\nlocal-output/\n'
        target.write_text(ignore_file(custom=custom))
        self.assertEqual(self.inspect().colour, 'green')
        self.assertFalse(prepare_upgrade(self.inspect(), {'.gitignore': 'use'}).changes)
        self.publish_ignore(ignore_file('*.log\n*.tmp\n'), '1.2.0')
        self.apply({'.gitignore': 'use'})
        self.assertTrue(target.read_text().endswith(custom))
        self.assertEqual(self.inspect().colour, 'green')
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True, capture_output=True)
        for path, ignored in [('other.log', True), ('keep.log', False), ('test.tmp', True)]:
            result = subprocess.run(['git', 'check-ignore', '-q', path], cwd=self.project)
            self.assertEqual(result.returncode, 0 if ignored else 1, path)

    def test_shared_project_edits_remain_visible_when_kept(self):
        self.publish_ignore()
        self.apply({'.gitignore': 'use'})
        target = self.project / '.gitignore'
        target.write_text(ignore_file('changed-shared-rule/\n', 'custom/\n'))
        self.apply({'.gitignore': 'keep'})
        self.assertEqual(self.inspect().colour, 'amber')
        self.apply({'.gitignore': 'use'})
        self.assertEqual(target.read_text(), ignore_file(custom='custom/\n'))
        self.assertEqual(self.inspect().colour, 'green')

    def test_bad_markers_are_rejected_without_overwriting_project_rules(self):
        self.publish_ignore()
        target = self.project / '.gitignore'
        broken = f'{IGNORE_SHARED}\n*.log\n# Missing project marker\ncustom/\n'
        target.write_text(broken)
        with self.assertRaisesRegex(SetupError, 'project-rules marker'):
            self.inspect()
        self.assertEqual(target.read_text(), broken)
        with self.assertRaisesRegex(SetupError, 'project-rules marker'):
            self.publish_ignore(broken, '1.2.0')

    def test_readme_choice_persists_and_new_template_content_requires_review(self):
        target = self.project / README_SOURCE
        target.write_text('My board documentation\n')
        self.assertEqual(self.inspect().colour, 'amber')
        self.apply({README_SOURCE: 'keep'})
        record = json.loads((self.project / STATE).read_text())
        self.assertEqual(record['files'][README_SOURCE]['choice'], 'keep')
        # Reopen from disk and continue editing the accepted project source.
        self.template = load_template(self.template.root)
        target.write_text('More board documentation\n')
        self.assertEqual(self.inspect().colour, 'green')
        item = next(i for i in self.inspect().items if i.path == README_SOURCE)
        self.assertEqual(item.status, ACCEPTED)
        self.assertIn('1 accepted customization', self.inspect().summary)
        (self.template.root / README_SOURCE).write_text('New default board documentation\n')
        self.template = release(self.template.root, '1.1.0')
        comparison = self.inspect()
        self.assertEqual(comparison.colour, 'amber')
        self.assertEqual([i.path for i in comparison.problems], [README_SOURCE])
        self.assertFalse(comparison.customizations)
        self.apply({README_SOURCE: 'keep'})
        self.assertEqual(self.inspect().colour, 'green')
        self.assertEqual(target.read_text(), 'More board documentation\n')

    def test_unrelated_template_update_does_not_invalidate_accepted_document(self):
        (self.project / README_SOURCE).write_text('My board documentation\n')
        self.apply({README_SOURCE: 'keep'})
        script = 'kibot_resources/scripts/helper.py'
        (self.template.root / script).write_text('print("updated")\n')
        self.template = release(self.template.root, '1.1.0')
        self.apply({script: 'use'})
        comparison = self.inspect()
        self.assertEqual(comparison.colour, 'green')
        self.assertEqual([i.path for i in comparison.customizations], [README_SOURCE])

    def test_older_records_keep_their_accepted_document_content(self):
        (self.project / README_SOURCE).write_text('Already accepted documentation\n')
        self.apply({README_SOURCE: 'keep'})
        state = self.project / STATE
        record = json.loads(state.read_text())
        del record['files'][README_SOURCE]['choice']
        state.write_text(json.dumps(record))
        self.assertEqual(self.inspect().colour, 'green')
        self.assertEqual([i.path for i in self.inspect().customizations], [README_SOURCE])

    def test_copying_the_project_and_source_preserves_the_accepted_choice(self):
        (self.project / README_SOURCE).write_text('Portable project README\n')
        self.apply({README_SOURCE: 'keep'})
        other_project = self.base / 'other-computer/project'
        other_template = self.base / 'other-computer/template'
        shutil.copytree(self.project, other_project)
        shutil.copytree(self.template.root, other_template)
        comparison = compare(other_project, load_template(other_template))
        self.assertEqual(comparison.colour, 'green')
        self.assertEqual([i.path for i in comparison.customizations], [README_SOURCE])
        self.assertNotIn(str(self.base), (other_project / STATE).read_text())

    def test_old_template_with_unmanaged_license_still_loads(self):
        (self.template.root / LICENSE_FILE).write_text('Old public licence\n')
        self.assertNotIn(LICENSE_FILE, load_template(self.template.root).files)
        self.template = release(self.template.root, '1.1.0')
        self.assertIn(LICENSE_FILE, self.template.files)
        self.assertEqual(self.inspect().colour, 'red')
        self.apply({LICENSE_FILE: 'use'})
        self.assertEqual((self.project / LICENSE_FILE).read_text(), 'Old public licence\n')

    def test_license_uses_normal_review_and_can_return_to_the_default(self):
        (self.template.root / LICENSE_FILE).write_text('Template licence\n')
        self.template = release(self.template.root, '1.1.0')
        target = self.project / LICENSE_FILE
        target.write_text('Project licence\n')
        (self.project / README_SOURCE).write_text('Project README source\n')
        self.apply({LICENSE_FILE: 'keep', README_SOURCE: 'keep'})
        comparison = self.inspect()
        self.assertEqual(comparison.colour, 'green')
        self.assertEqual({i.path for i in comparison.customizations}, {LICENSE_FILE, README_SOURCE})
        self.assertIn('2 accepted customizations', comparison.summary)
        backup = self.apply({LICENSE_FILE: 'use'})
        self.assertEqual((backup / LICENSE_FILE).read_text(), 'Project licence\n')
        self.assertEqual(target.read_text(), 'Template licence\n')
        self.assertEqual(next(i for i in self.inspect().items if i.path == LICENSE_FILE).status, 'Matches')
        self.assertEqual([i.path for i in self.inspect().customizations], [README_SOURCE])
        self.assertEqual(json.loads((self.project / STATE).read_text())['files'][LICENSE_FILE]['choice'], 'use')
        target.write_text('Changed after restoring the default\n')
        self.assertEqual(self.inspect().colour, 'amber')

    def test_bad_recorded_choice_is_not_silently_accepted(self):
        self.apply({README_SOURCE: 'keep'})
        state = self.project / STATE
        record = json.loads(state.read_text())
        record['files'][README_SOURCE]['choice'] = 'anything'
        state.write_text(json.dumps(record))
        with self.assertRaisesRegex(SetupError, 'Invalid recorded document choice'):
            self.inspect()

    def test_default_readme_reset_only_changes_source_and_records_with_backup(self):
        target = self.project / README_SOURCE
        target.write_text('My board documentation\n')
        (self.project / 'README.md').write_text('Generated README remains until CI\n')
        script = self.project / 'kibot_resources/scripts/helper.py'
        script.write_text('Pending project script\n')
        board = self.project / 'sample.kicad_pcb'
        board_before = board.read_bytes()
        plan = prepare_upgrade(self.inspect(), {README_SOURCE: 'use'})
        self.assertEqual({c.relative for c in plan.changes}, {README_SOURCE, STATE})
        backup = apply_upgrade(plan, self.storage)
        self.assertEqual((backup / README_SOURCE).read_text(), 'My board documentation\n')
        self.assertEqual(target.read_bytes(), self.template.files[README_SOURCE])
        self.assertEqual((self.project / 'README.md').read_text(), 'Generated README remains until CI\n')
        self.assertEqual(script.read_text(), 'Pending project script\n')
        self.assertEqual(board.read_bytes(), board_before)
        self.assertFalse(prepare_upgrade(self.inspect(), {README_SOURCE: 'use'}).changes)

    def test_missing_readme_source_remains_a_required_file(self):
        (self.project / README_SOURCE).unlink()
        self.assertEqual(self.inspect().colour, 'red')
        self.apply({README_SOURCE: 'use'})
        self.assertEqual((self.project / README_SOURCE).read_bytes(), self.template.files[README_SOURCE])


if __name__ == '__main__':
    unittest.main()
