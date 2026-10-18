"""Validate the built package against KiCad's official v1 schemas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile

import jsonschema

from scripts.build_release import validate_plugin_identifier


def validate(schema: dict, document: dict, definition: str | None = None) -> None:
    schema = dict(schema)
    if definition:
        schema["$ref"] = "#/definitions/" + definition
    jsonschema.Draft7Validator(schema).validate(document)
    if 'identifier' in document:
        validate_plugin_identifier(document['identifier'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schemas", type=Path, required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--site", type=Path)
    args = parser.parse_args()
    pcm = json.loads((args.schemas / "pcm.v1.schema.json").read_text())
    ipc = json.loads((args.schemas / "api.v1.schema.json").read_text())
    archives = list(args.dist.glob("*.zip"))
    if not archives:
        raise ValueError("No package was built")
    for archive in archives:
        with zipfile.ZipFile(archive) as package:
            validate(pcm, json.loads(package.read("metadata.json")))
            validate(ipc, json.loads(package.read("plugins/kicad_project_manager/plugin.json")))
    for metadata in args.dist.glob("*.pcm.json"):
        validate(pcm, json.loads(metadata.read_text()))
    if args.site:
        validate(pcm, json.loads((args.site / "repository.json").read_text()), "Repository")
        validate(pcm, json.loads((args.site / "packages.json").read_text()), "PackageArray")
    print("IPC manifest, PCM package, and requested feed schemas passed")


if __name__ == "__main__":
    main()
