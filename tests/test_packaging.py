from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from scripts.build_release import ROOT, build, source_version
from scripts.build_catalog import merge_packages, write_catalog


class PackageTests(unittest.TestCase):
    def test_deterministic_archive_manifest_and_download_integrity(self):
        with TemporaryDirectory() as folder:
            output = Path(folder)
            archive, metadata_path = build(ROOT, output, "v" + source_version(), "coprod-LLC/kicad_project_manager")
            original = archive.read_bytes()
            metadata = json.loads(metadata_path.read_text())
            version = metadata["versions"][0]
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
            build(ROOT, output, "v" + source_version(), "coprod-LLC/kicad_project_manager")
            self.assertEqual(original, archive.read_bytes())

    def test_release_tag_must_match_source(self):
        with TemporaryDirectory() as folder, self.assertRaises(ValueError):
            build(ROOT, Path(folder), "v99.0.0", "coprod-LLC/kicad_project_manager")

    def test_feed_retains_old_versions_and_sorts_numerically(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            _, metadata_path = build(ROOT, root / "dist", "v" + source_version(), "coprod-LLC/kicad_project_manager")
            first = json.loads(metadata_path.read_text())
            next_version = deepcopy(first)
            next_version["versions"][0]["version"] = "0.10.0"
            next_version['description'] = 'Latest package description'
            package = merge_packages([first, next_version, first])
            self.assertEqual([entry["version"] for entry in package["versions"]], ["0.10.0", source_version()])
            self.assertEqual(package['description'], 'Latest package description')
            write_catalog(package, root / "site", "https://example.org/plugins", 12345)
            repository = json.loads((root / "site/repository.json").read_text())
            actual_hash = hashlib.sha256((root / "site/packages.json").read_bytes()).hexdigest()
            self.assertEqual(repository["packages"]["sha256"], actual_hash)
            self.assertEqual(repository["packages"]["url"], "https://example.org/plugins/packages.json")

    def test_feed_refuses_replacing_published_version(self):
        package = {"identifier": "com.github.coprod-llc.kicad-project-manager", "versions": [{"version": "0.1.0", "download_sha256": "a"}]}
        changed = deepcopy(package)
        changed["versions"][0]["download_sha256"] = "b"
        with self.assertRaises(ValueError):
            merge_packages([package, changed])


if __name__ == "__main__":
    unittest.main()
