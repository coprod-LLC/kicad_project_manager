"""Publish/verify a local template inventory without any company data in the plugin."""
from __future__ import annotations

import argparse
from pathlib import Path

from project_manager.setup import project_path, read_file
from project_manager.templates import MANIFEST, build_manifest, load_template
from project_manager.yaml_fields import SetupError

INFO = 'meta/info.html'
VERSION_START = b'<!-- kpm:version:start -->'
VERSION_END = b'<!-- kpm:version:end -->'


def version_badge(root: Path, version: str) -> tuple[bytes, bytes]:
    """Replace one explicit badge region, preserving every other HTML byte."""
    original = read_file(root, INFO)
    if (original is None or original.count(VERSION_START) != 1
            or original.count(VERSION_END) != 1):
        raise SetupError(f'{INFO} must contain exactly one pair of '
                         '<!-- kpm:version:start --> / <!-- kpm:version:end --> markers.')
    start = original.index(VERSION_START) + len(VERSION_START)
    end = original.index(VERSION_END)
    if end < start:
        raise SetupError(f'The version markers in {INFO} are reversed.')
    updated = original[:start] + f'Template v{version}'.encode('ascii') + original[end:]
    return original, updated


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--id', dest='identity')
    parser.add_argument('--version')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    if not args.check and (not args.identity or not args.version):
        parser.error('--id and --version are required when generating a manifest')
    try:
        root = args.template.expanduser().resolve(strict=True)
        if not args.check:
            # Validate both outputs before writing either one. No broad HTML substitution.
            data = build_manifest(root, args.identity, args.version)
            before, after = version_badge(root, args.version)
            target = project_path(root, MANIFEST)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_bytes() != data:
                target.write_bytes(data)
            if before != after:
                (root / INFO).write_bytes(after)
        template = load_template(root)
        before, after = version_badge(root, template.version)
        if before != after:
            raise SetupError(f'{INFO} version badge does not match template.json '
                             f'({template.version}). Regenerate the manifest to synchronize it.')
    except (SetupError, OSError) as exc:
        parser.exit(1, f'Template validation failed: {exc}\n')
    print(f'{template.identity} {template.version}: verified {len(template.files)} managed files and HTML version badge')


if __name__ == '__main__':
    main()
