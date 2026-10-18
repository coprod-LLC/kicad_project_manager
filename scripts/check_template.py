"""Exercise setup on a disposable public KDT template and verify KiBot output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess

from project_manager.setup import apply, prepare, read_setup

EXPECTED = {
    'PROJECT_NAME': 'Example controller', 'BOARD_NAME': 'Control board',
    'COMPANY': 'Example Company', 'DESIGNER': 'Alice Martin; Zoë Chen',
    'GIT_URL': 'https://example.org/team/controller',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'verify'])
    parser.add_argument('--template', type=Path)
    parser.add_argument('--project', type=Path, required=True)
    args = parser.parse_args()
    root = args.project.resolve()
    if args.mode == 'prepare':
        if args.template is None:
            parser.error('--template is required for prepare')
        shutil.copytree(args.template, root, ignore=shutil.ignore_patterns('.git'))
        snapshot = read_setup(root)
        metadata = {**snapshot.metadata, **EXPECTED}
        apply(prepare(snapshot, metadata, 'PRELIMINARY'), root.parent / 'test-settings')
        saved = read_setup(root)
        assert saved.metadata == metadata and saved.status == 'PRELIMINARY'
        assert not prepare(saved, metadata, 'PRELIMINARY').changes
        # Real template imports/preflights/report output, with a minimal report
        # body so the test can verify every metadata field and both authors.
        report = root / 'kibot_resources/templates/readme.txt'
        report.write_text(''.join(key + ': ${' + key + '}\n' for key in EXPECTED)
                          + 'VARIANT: ${VARIANT}\n', encoding='utf-8')
        # Recent KiBot reports collect all configured images by default. This
        # metadata test does not need to render the entire manufacturing pack.
        report_config = root / 'kibot_yaml/kibot_out_md_report.yaml'
        contents = report_config.read_text(encoding='utf-8')
        assert contents.count('    options:\n') == 1
        report_config.write_text(contents.replace('    options:\n', "    options:\n      collect_images: 'no'\n"), encoding='utf-8')
        for command in [['init'], ['config', 'user.name', 'Integration Test'],
                        ['config', 'user.email', 'integration@example.org'],
                        ['add', '.'], ['commit', '-m', 'Synthetic setup integration project']]:
            subprocess.run(['git', '-C', str(root), *command], check=True, capture_output=True)
        print('Disposable public template prepared; metadata and documentation status verified.')
    else:
        project_file = root / 'KDT_Hierarchical_KiBot.kicad_pro'
        variables = json.loads(project_file.read_text(encoding='utf-8'))['text_variables']
        for key, expected in {**EXPECTED, 'VARIANT': 'PRELIMINARY'}.items():
            assert variables.get(key) == expected, (key, variables.get(key), expected)
        report = (root / 'README.md').read_text(encoding='utf-8')
        for key, expected in {**EXPECTED, 'VARIANT': 'PRELIMINARY'}.items():
            assert key + ': ' + expected in report, (key, report)
        print('KiBot project text variables and generated report contain every edited value.')


if __name__ == '__main__':
    main()
