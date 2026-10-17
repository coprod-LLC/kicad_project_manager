"""Publishing checks synchronize the native template description and its manifest."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from project_manager.templates import MANIFEST, load_template
from scripts.template_manifest import INFO, VERSION_START, VERSION_END, main
from tests.test_templates import template_folder


class ManifestCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'template'
        template_folder(self.root)
        (self.root / 'meta').mkdir()
        self.html = (b'\xef\xbb\xbf<html>\r\n<title>Example</title>\r\n<p><b>' + VERSION_START
                     + b'Template v1.0.0' + VERSION_END + b'</b></p>\r\n'
                     b'<p>Unrelated release 1.0.0 stays unchanged.</p>\r\n</html>\r\n')
        (self.root / INFO).write_bytes(self.html)

    def run_command(self, *args):
        with redirect_stdout(StringIO()) as output:
            main(['--template', str(self.root), *args])
        return output.getvalue()

    def assert_failure(self, message, *args):
        with redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit) as raised:
            self.run_command(*args)
        self.assertEqual(raised.exception.code, 1)
        self.assertIn(message, error.getvalue())

    def test_generation_updates_only_badge_and_preserves_html_bytes(self):
        self.run_command('--id', 'example.template', '--version', '1.2.3')
        self.assertEqual(load_template(self.root).version, '1.2.3')
        self.assertEqual((self.root / INFO).read_bytes(),
                         self.html.replace(b'Template v1.0.0', b'Template v1.2.3'))
        self.assertIn('HTML version badge', self.run_command('--check'))

    def test_check_rejects_stale_badge_without_changing_either_file(self):
        stale = self.html.replace(b'Template v1.0.0', b'Template v0.9.0')
        (self.root / INFO).write_bytes(stale)
        manifest = (self.root / MANIFEST).read_bytes()
        self.assert_failure('does not match template.json (1.0.0)', '--check')
        self.assertEqual((self.root / INFO).read_bytes(), stale)
        self.assertEqual((self.root / MANIFEST).read_bytes(), manifest)

    def test_missing_duplicate_and_reversed_markers_abort_before_manifest_write(self):
        for html in (self.html.replace(VERSION_START, b''), self.html.replace(VERSION_END, b''),
                     self.html + VERSION_START, self.html + VERSION_END,
                     VERSION_END + b'Template v1.0.0' + VERSION_START):
            with self.subTest(html=html):
                (self.root / INFO).write_bytes(html)
                manifest = (self.root / MANIFEST).read_bytes()
                self.assert_failure('markers', '--id', 'example.template', '--version', '2.0.0')
                self.assertEqual((self.root / MANIFEST).read_bytes(), manifest)
                self.assertEqual((self.root / INFO).read_bytes(), html)
                self.assert_failure('markers', '--check')

    def test_missing_html_fails_with_actionable_error(self):
        (self.root / INFO).unlink()
        self.assert_failure('meta/info.html must contain exactly one pair', '--check')

    def test_matching_badge_and_generation_are_idempotent(self):
        before = (self.root / INFO).stat().st_mtime_ns
        self.run_command('--id', 'example.template', '--version', '1.0.0')
        self.assertEqual((self.root / INFO).stat().st_mtime_ns, before)
        manifest_time = (self.root / MANIFEST).stat().st_mtime_ns
        self.run_command('--id', 'example.template', '--version', '1.0.0')
        self.assertEqual((self.root / MANIFEST).stat().st_mtime_ns, manifest_time)


if __name__ == '__main__':
    unittest.main()
