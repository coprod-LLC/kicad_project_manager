"""Link a checkout into the KiCad 9 IPC plugins folder without replacing installs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_release import validate_plugin_identifier


def plugin_directory() -> Path:
    if os.environ.get("KICAD_DOCUMENTS_HOME"):
        base = Path(os.environ["KICAD_DOCUMENTS_HOME"])
    elif sys.platform == "linux":
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "kicad"
    else:
        base = Path.home() / "Documents/KiCad"
    return base / "9.0/plugins"


def pcm_installations(plugins_dir: Path) -> list[Path]:
    """Recognize installed copies by manifest name, including earlier package IDs."""
    root = plugins_dir.parent / "3rdparty/plugins"
    matches = []
    if root.is_dir():
        for folder in root.iterdir():
            for manifest in (folder / "plugin.json", folder / "kicad_project_manager/plugin.json"):
                try:
                    document = json.loads(manifest.read_text(encoding="utf-8"))
                    if isinstance(document, dict) and document.get("name") == "KiCad Project Manager":
                        matches.append(folder)
                        break
                except (OSError, ValueError):
                    continue
    return matches


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugins-dir", type=Path, default=plugin_directory())
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    manifest = json.loads((source / 'plugin.json').read_text(encoding='utf-8'))
    validate_plugin_identifier(manifest['identifier'])
    destination = args.plugins_dir / source.name
    if destination.is_symlink() and destination.resolve() == source:
        print(f"Already linked: {destination}")
        return
    if destination.exists() or destination.is_symlink():
        raise SystemExit(f"An installation already exists at {destination}; it was left untouched.")
    if pcm_installations(args.plugins_dir):
        raise SystemExit("Uninstall the PCM copy before enabling a development checkout.")
    args.plugins_dir.mkdir(parents=True, exist_ok=True)
    try:
        destination.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        raise SystemExit(f"Could not create link: {exc}. On Windows, use Developer Mode or copy the checkout.") from exc
    print(f"Linked {destination} → {source}")
    print("Restart PCB Editor with the KiCad API enabled; allow the plugin environment to finish installing.")


if __name__ == "__main__":
    main()
