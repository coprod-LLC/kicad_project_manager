from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from project_manager import IDENTIFIER
from scripts.install_dev import pcm_installations

from scripts.build_release import ROOT, build, source_version
from scripts.build_catalog import main as publish_catalog, merge_packages, write_catalog


class PackageTests(unittest.TestCase):
    def test_deterministic_archive_manifest_and_download_integrity(self):
        with TemporaryDirectory() as folder:
            output = Path(folder)
            archive, metadata_path = build(ROOT, output, "v" + source_version(), "example/project-manager")
            original = archive.read_bytes()
            metadata = json.loads(metadata_path.read_text())
            version = metadata["versions"][0]
            self.assertEqual(version["status"], "stable")
            self.assertEqual((version["kicad_version"], version["kicad_version_max"]), ("9.0", "10.99"))
            self.assertEqual(version["download_sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(version["download_size"], len(original))
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(version["install_size"], sum(item.file_size for item in package.infolist()))
                names = package.namelist()
                self.assertTrue(all(name == "metadata.json" or name.startswith(("plugins/kicad_project_manager/", "resources/")) for name in names))
                self.assertFalse(any(".git" in name or "tests/" in name or "__pycache__" in name for name in names))
                manifest = json.loads(package.read("plugins/kicad_project_manager/plugin.json"))
                self.assertEqual(manifest["identifier"], metadata["identifier"])
                for action in manifest["actions"]:
                    self.assertIn("plugins/kicad_project_manager/" + action["entrypoint"], names)
                    for icon in action["icons-light"]:
                        self.assertIn("plugins/kicad_project_manager/" + icon, names)
            build(ROOT, output, "v" + source_version(), "example/project-manager")
            self.assertEqual(original, archive.read_bytes())

    def test_release_tag_must_match_source(self):
        with TemporaryDirectory() as folder, self.assertRaises(ValueError):
            build(ROOT, Path(folder), "v99.0.0", "example/project-manager")

    def test_build_rejects_schema_valid_identifier_that_kicad_cannot_discover(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('plugin.json', 'pcm/metadata.json', 'project_manager/__init__.py'):
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((ROOT / name).read_bytes())
            for name in ('plugin.json', 'pcm/metadata.json'):
                path = root / name
                document = json.loads(path.read_text())
                document['identifier'] = 'org.example-plugin'
                path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, 'at least three parts'):
                build(root, root / 'dist', 'v' + source_version(), 'example/project-manager')
            self.assertFalse((root / 'dist').exists())

    def test_feed_retains_old_versions_and_sorts_numerically(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            _, metadata_path = build(ROOT, root / "dist", "v" + source_version(), "example/project-manager")
            first = json.loads(metadata_path.read_text())
            first["versions"][0]["version"] = "0.9.0"
            next_version = deepcopy(first)
            next_version["versions"][0]["version"] = "0.10.0"
            next_version['description'] = 'Latest package description'
            package = merge_packages([first, next_version, first])
            self.assertEqual([entry["version"] for entry in package["versions"]], ["0.10.0", "0.9.0"])
            self.assertEqual(package['description'], 'Latest package description')
            write_catalog(package, root / "site", "https://example.org/plugins", 12345)
            repository = json.loads((root / "site/repository.json").read_text())
            actual_hash = hashlib.sha256((root / "site/packages.json").read_bytes()).hexdigest()
            self.assertEqual(repository["packages"]["sha256"], actual_hash)
            self.assertEqual(repository["packages"]["url"], "https://example.org/plugins/packages.json")
            homepage = (root / "site/index.html").read_text()
            self.assertIn("KiCad 9 and 10", homepage)
            self.assertIn("(stable)", homepage)

    def test_feed_refuses_replacing_published_version(self):
        package = {"identifier": IDENTIFIER, "versions": [{"version": "0.1.0", "download_sha256": "a"}]}
        changed = deepcopy(package)
        changed["versions"][0]["download_sha256"] = "b"
        with self.assertRaises(ValueError):
            merge_packages([package, changed])

    def test_package_identity_is_generic_and_consistent(self):
        manifest = json.loads((ROOT / 'plugin.json').read_text())
        metadata = json.loads((ROOT / 'pcm/metadata.json').read_text())
        self.assertEqual(IDENTIFIER, 'org.kicadprojectmanager.plugin')
        self.assertEqual(manifest['identifier'], IDENTIFIER)
        self.assertEqual(metadata['identifier'], IDENTIFIER)
        with self.assertRaisesRegex(ValueError, 'Unexpected package identifier'):
            merge_packages([{'identifier': 'org.previous-package', 'versions': []}])

    def test_development_installer_detects_pcm_with_any_package_identity(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            plugins = root / 'plugins'
            previous = root / '3rdparty/plugins/previous-package/kicad_project_manager'
            previous.mkdir(parents=True)
            (previous / 'plugin.json').write_text(json.dumps({'identifier': 'org.previous-package',
                                                             'name': 'KiCad Project Manager'}))
            self.assertEqual(pcm_installations(plugins), [previous.parent])
            (previous / 'plugin.json').write_text('{invalid JSON')
            self.assertFalse(pcm_installations(plugins))

    def test_new_feed_excludes_previous_package_identity(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            _, metadata_path = build(ROOT, root / 'dist', 'v' + source_version(), 'example/project-manager')
            previous = json.loads(metadata_path.read_text())
            previous['identifier'] = 'org.previous-package'
            previous['versions'][0]['version'] = '0.1.0'
            releases = root / 'releases.json'
            releases.write_text('[]')
            args = ['build_catalog', '--releases', str(releases), '--current', str(metadata_path),
                    '--base-url', 'https://example.org/plugins', '--output', str(root / 'site')]
            with patch('sys.argv', args), patch('scripts.build_catalog.released_packages', return_value=[previous]), \
                 patch('builtins.print'):
                publish_catalog()
            packages = json.loads((root / 'site/packages.json').read_text())['packages']
            self.assertEqual([package['identifier'] for package in packages], [IDENTIFIER])
            self.assertEqual([version['version'] for version in packages[0]['versions']], [source_version()])


if __name__ == "__main__":
    unittest.main()
