"""Build a reproducible PCM package from an explicit list of public files."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = (
    "plugin.json", "requirements.txt", "run_project_manager.py", "LICENSE", "README.md",
    "project_manager/__init__.py", "project_manager/context.py", "project_manager/ui.py",
    "resources/icon.png",
)


def source_version(root: Path = ROOT) -> str:
    module = ast.parse((root / "project_manager/__init__.py").read_text(encoding="utf-8"))
    for statement in module.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in statement.targets
        ):
            return ast.literal_eval(statement.value)
    raise ValueError("Missing __version__")


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(root: Path, output: Path, tag: str, repository: str) -> tuple[Path, Path]:
    version = source_version(root)
    if not re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,6}", version) or tag != "v" + version:
        raise ValueError(f"Tag {tag!r} must match source version v{version}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Repository must be OWNER/REPO")
    metadata = json.loads((root / "pcm/metadata.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    if metadata["identifier"] != manifest["identifier"]:
        raise ValueError("IPC and PCM identifiers must match")
    entry = {
        "version": version, "status": "testing", "runtime": "ipc",
        "kicad_version": "9.0", "kicad_version_max": "9.99",
        "platforms": ["windows", "macos", "linux"],
    }
    metadata["versions"] = [entry]
    payload = {"metadata.json": json_bytes(metadata)}
    for relative in PAYLOAD:
        source = root / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Missing file or symlink in package: {relative}")
        payload[f"plugins/kicad_project_manager/{relative}"] = source.read_bytes()
    payload["resources/icon.png"] = (root / "resources/icon.png").read_bytes()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"kicad_project_manager-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, content in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            target.writestr(info, content)
    data = archive.read_bytes()
    entry.update({
        "download_url": f"https://github.com/{repository}/releases/download/{tag}/{archive.name}",
        "download_sha256": sha256(data), "download_size": len(data),
        "install_size": sum(map(len, payload.values())),
    })
    index_file = output / f"kicad_project_manager-{version}.pcm.json"
    index_file.write_bytes(json_bytes(metadata))
    (output / "SHA256SUMS").write_text(f"{sha256(data)}  {archive.name}\n", encoding="utf-8")
    return archive, index_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", default="coprod-LLC/kicad_project_manager")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    archive, metadata = build(ROOT, args.output, args.tag, args.repository)
    print(f"Built {archive.name} and {metadata.name}")


if __name__ == "__main__":
    main()
