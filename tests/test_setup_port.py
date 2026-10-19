from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from scripts.setup_port import directories, install, library_table


class PortableSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.repos = {}
        for name in ('kicad_project_manager', 'kicad_library_manager', 'kicad_coprod_template_kibot',
                     'kicad_coprod_lib', 'kicad_coprod_standards'):
            repo = self.root / name
            repo.mkdir()
            self.repos[name] = repo
        for name in ('kicad_project_manager', 'kicad_library_manager'):
            (self.repos[name] / 'plugin.json').write_text(json.dumps({'name': name}))
        resources = self.repos['kicad_coprod_template_kibot'] / 'kibot_resources'
        (resources / 'fonts').mkdir(parents=True)
        (resources / 'fonts/Port Test.ttf').write_bytes(b'disposable font data')
        (resources / 'colors').mkdir()
        (resources / 'colors/Altium_Theme.json').write_text('{}')
        library = self.repos['kicad_coprod_lib']
        for name in ('Symbols', 'Footprints', 'Database'):
            (library / name).mkdir()
        (library / 'Symbols/Company Ω.kicad_sym').write_text('(kicad_symbol_lib)')
        (library / 'Footprints/Company Ω.pretty').mkdir()
        (library / 'Database/#coprod.kicad_dbl').write_text('{}')

    def test_mac_install_is_idempotent_and_leaves_version9_unchanged(self):
        legacy = self.home / 'Library/Preferences/kicad/9.0/kicad_common.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"keep":"KiCad 9 settings"}')
        before = legacy.read_bytes()
        python = Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3')
        paths = directories(self.home, 'darwin')
        paths['config'].mkdir(parents=True)
        common = paths['config'] / 'kicad_common.json'
        common.write_text('{"keep":"KiCad 10 settings"}')
        install(self.repos, self.home, 'darwin', python)
        self.assertEqual(legacy.read_bytes(), before)
        self.assertEqual((self.home / 'Library/Fonts/Port Test.ttf').read_bytes(), b'disposable font data')
        self.assertTrue((paths['config'] / 'colors/Altium_Theme.json').is_file())
        self.assertEqual(json.loads(common.read_text())['keep'], 'KiCad 10 settings')
        self.assertTrue(json.loads(common.read_text())['api']['enable_server'])
        self.assertEqual((paths['plugins'] / 'kicad_library_manager').resolve(), self.repos['kicad_library_manager'])
        self.assertEqual(json.loads(paths['library'].read_text())['repo_path'], str(self.repos['kicad_coprod_lib']))
        before_files = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file() and not p.is_symlink()}
        install(self.repos, self.home, 'darwin', python)
        self.assertEqual(before_files, {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file() and not p.is_symlink()})

    def test_conflicting_existing_library_is_left_untouched(self):
        table = self.root / 'sym-lib-table'
        table.write_text('(sym_lib_table (lib (name "Company") (type "KiCad") (uri "/existing.kicad_sym")))')
        before = table.read_bytes()
        with self.assertRaisesRegex(ValueError, 'left untouched'):
            library_table(table, 'sym_lib_table', {'Company': ('KiCad', self.root / 'different.kicad_sym')})
        self.assertEqual(before, table.read_bytes())

    def test_normal_plugin_installation_is_not_overwritten(self):
        target = directories(self.home, 'darwin')['plugins'] / 'kicad_project_manager'
        target.mkdir(parents=True)
        marker = target / 'existing.txt'
        marker.write_text('preserve')
        with self.assertRaisesRegex(ValueError, 'Another installation'):
            install(self.repos, self.home, 'darwin', Path('/python'))
        self.assertEqual(marker.read_text(), 'preserve')


if __name__ == '__main__':
    unittest.main()
