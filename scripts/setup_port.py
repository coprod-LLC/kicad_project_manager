"""Install the five checkouts for a KiCad 10 trial; leave KiCad 9 untouched."""
from __future__ import annotations

import argparse
import configparser
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from project_manager.sexpr import atoms, forms, spans

REPOSITORIES = {
    'kicad_coprod_template_kibot': 'coprod-LLC',
    'kicad_coprod_standards': 'coprod-LLC',
    'kicad_coprod_lib': 'coprod-LLC',
    'kicad_library_manager': 'theohg-coprod',
}
BRANCH = 'port/kicad10'


def run(command: list[str], **kwargs) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, **kwargs).strip()


def find_cli(platform: str, home: Path) -> Path:
    override = os.environ.get('KICAD_CLI')
    candidates = [Path(override)] if override else []
    if platform == 'darwin':
        for root in (Path('/Applications'), home / 'Applications'):
            candidates.extend(root.glob('KiCad*/KiCad.app/Contents/MacOS/kicad-cli'))
            candidates.extend(root.glob('KiCad*.app/Contents/MacOS/kicad-cli'))
    found = shutil.which('kicad-cli')
    if found:
        candidates.append(Path(found))
    for path in dict.fromkeys(candidates):
        try:
            if run([str(path), 'version'], timeout=10).split('.')[0] == '10':
                return path
        except (OSError, subprocess.SubprocessError):
            pass
    raise ValueError('Install KiCad 10 first, or pass --cli /path/to/its/kicad-cli.')


def find_python(cli: Path) -> Path:
    contents = cli.parent.parent
    candidates = [contents / 'Frameworks/Python.framework/Versions/Current/bin/python3',
                  cli.parent / 'python3.11', cli.parent / 'python3', Path(sys.executable)]
    for path in candidates:
        try:
            run([str(path), '-c', 'import sys,ssl,venv,wx; assert sys.version_info >= (3,10); from wx.svg import SVGimage'], timeout=15)
            return path
        except (OSError, subprocess.SubprocessError):
            pass
    raise ValueError('KiCad Python with wxPython was not found; pass --python /path/to/bundled/python3.')


def directories(home: Path, platform: str) -> dict[str, Path]:
    if platform == 'darwin':
        config, data = home / 'Library/Preferences/kicad/10.0', home / 'Library/Application Support'
        documents = home / 'Documents/KiCad/10.0'
    elif platform == 'win32':
        config, data = home / 'AppData/Roaming/kicad/10.0', home / 'AppData/Local'
        documents = home / 'Documents/KiCad/10.0'
    else:
        config, data = home / '.config/kicad/10.0', home / '.local/share'
        documents = data / 'kicad/10.0'
    return {'config': config, 'plugins': documents / 'plugins', 'templates': documents / 'template',
            'manager': data / 'kicad_project_manager/10.0/settings.json',
            'library': home / '.config/kicad_library_manager/10.0/config.json'}


def write(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding='utf-8') == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + '.pre-port-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        shutil.copy2(path, backup)
    temporary = path.with_name(path.name + '.port-tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.replace(path)


def update_json(path: Path, edit) -> None:
    value = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(value, dict):
        raise ValueError(f'{path} must contain a JSON object; it was left untouched.')
    edit(value)
    write(path, json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def link(source: Path, target: Path) -> None:
    if target.is_symlink() and target.resolve() == source.resolve():
        return
    if target.exists() or target.is_symlink():
        raise ValueError(f'Another installation exists at {target}. Remove that KiCad 10 copy before rerunning.')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source.resolve(), target_is_directory=True)


def library_table(path: Path, root_name: str, entries: dict[str, tuple[str, Path]]) -> None:
    if path.exists():
        text = path.read_text()
    else:
        text = (f'({root_name} (version 7)\n  (lib (name "KiCad") (type "Table") '
                f'(uri "${{KICAD10_TEMPLATE_DIR}}/{path.name}") (options "") (descr "KiCad libraries"))\n)\n')
    old = {}
    for name, block in forms(text, children=True):
        if name == 'lib':
            fields = {key: atoms(value)[1:] for key, value in forms(block, children=True)}
            if fields.get('name'):
                old[fields['name'][0]] = (fields.get('uri') or [''])[0]
    extra = []
    for nickname, (kind, source) in entries.items():
        uri = source.resolve().as_posix()
        if nickname in old:
            if old[nickname] != uri:
                raise ValueError(f'{nickname} already points elsewhere in {path}; its entry was left untouched.')
            continue
        quoted = lambda value: json.dumps(value, ensure_ascii=False)
        extra.append(f'  (lib (name {quoted(nickname)}) (type {quoted(kind)}) '
                     f'(uri {quoted(uri)}) (options "") (descr "coprod port trial"))\n')
    if extra:
        end = text.rfind(')')
        text = text[:end] + '\n' + ''.join(extra) + text[end:]
    write(path, text)


def checkouts(root: Path, *, clone: bool) -> dict[str, Path]:
    result = {'kicad_project_manager': root}
    for name, owner in REPOSITORIES.items():
        sibling = root.parent / name
        try:
            compatible = run(['git', '-C', str(sibling), 'branch', '--show-current']) == BRANCH
        except (OSError, subprocess.SubprocessError):
            compatible = False
        selected = sibling if compatible else root / '.port-repos' / name
        if not selected.is_dir():
            if not clone:
                raise ValueError(f'Missing {name}; rerun without --no-clone.')
            selected.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['git', 'clone', '--branch', BRANCH, f'git@github.com:{owner}/{name}.git', str(selected)], check=True)
        if run(['git', '-C', str(selected), 'branch', '--show-current']) != BRANCH:
            raise ValueError(f'{selected} must use {BRANCH}; no branch was switched.')
        result[name] = selected.resolve()
    return result


def install(repos: dict[str, Path], home: Path, platform: str, python: Path) -> dict[str, str]:
    paths = directories(home, platform)
    for name in ('kicad_project_manager', 'kicad_library_manager'):
        # A PCM copy with the same identifier would hide or duplicate the dev link.
        pcm = paths['plugins'].parent / '3rdparty/plugins'
        if pcm.exists():
            for manifest in pcm.rglob('plugin.json'):
                if json.loads(manifest.read_text()).get('name') == json.loads((repos[name] / 'plugin.json').read_text()).get('name'):
                    raise ValueError(f'Uninstall the KiCad 10 PCM copy at {manifest.parent} first.')
        link(repos[name], paths['plugins'] / name)
    link(repos['kicad_coprod_template_kibot'], paths['templates'] / 'coprod-kicad10')
    resources = repos['kicad_coprod_template_kibot'] / 'kibot_resources'
    font_dir = home / 'Library/Fonts' if platform == 'darwin' else home / '.local/share/fonts'
    if platform != 'win32':
        for font in (resources / 'fonts').glob('*'):
            if font.suffix.lower() in ('.ttf', '.otf'):
                target = font_dir / font.name
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(font, target)
    theme = resources / 'colors/Altium_Theme.json'
    target = paths['config'] / 'colors/Altium_Theme.json'
    if theme.exists() and not target.exists():
        write(target, theme.read_text(encoding='utf-8'))
    blocks = paths['config'] / 'design-block-lib-table'
    if not blocks.exists():
        write(blocks, '(design_block_lib_table (version 7))\n')
    update_json(paths['config'] / 'kicad_common.json',
                lambda data: data.setdefault('api', {}).update(enable_server=True, interpreter_path=str(python)))
    def manager(data):
        data.setdefault('template_directories', {})['coprod.kicad-kibot'] = str(repos['kicad_coprod_template_kibot'])
        data['standards_directory'] = str(repos['kicad_coprod_standards'])
    update_json(paths['manager'], manager)
    update_json(paths['library'], lambda data: data.update(repo_path=str(repos['kicad_coprod_lib'])))
    library = repos['kicad_coprod_lib']
    symbols = {p.stem: ('KiCad', p) for p in (library / 'Symbols').glob('*.kicad_sym')}
    symbols.update({p.stem: ('Database', p) for p in (library / 'Database').glob('*.kicad_dbl')})
    footprints = {p.stem: ('KiCad', p) for p in (library / 'Footprints').glob('*.pretty')}
    library_table(paths['config'] / 'sym-lib-table', 'sym_lib_table', symbols)
    library_table(paths['config'] / 'fp-lib-table', 'fp_lib_table', footprints)
    return {key: str(value) for key, value in paths.items()}


def setup_mac_database(repos: dict[str, Path]) -> None:
    # Existing KiCad 9 users may have iODBC's native Mac registration already.
    for file in (Path.home() / 'Library/ODBC/odbcinst.ini', Path.home() / '.odbcinst.ini',
                 Path('/Library/ODBC/odbcinst.ini')):
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read(file)
        driver = config.get('SQLite3 ODBC Driver', 'Driver', fallback='')
        if driver and Path(driver).expanduser().is_file():
            print(f'Reusing the existing SQLite ODBC registration in {file}.')
            return
    env = os.environ.copy()
    env['PATH'] = '/opt/homebrew/bin:/usr/local/bin:' + env.get('PATH', '')
    odbc = shutil.which('odbcinst', path=env['PATH'])
    if odbc:
        result = subprocess.run([odbc, '-q', '-d', '-n', 'SQLite3 ODBC Driver'],
                                capture_output=True, text=True, env=env)
        if result.returncode == 0:
            print('Reusing the existing SQLite ODBC driver registration.')
            return
    if not shutil.which('brew', path=env['PATH']):
        raise ValueError('Database parts need the SQLite ODBC driver. Install Homebrew first '
                         '(https://brew.sh), then rerun setup. Use --skip-database for a plugin-only trial.')
    # The existing driver setup installs a small Homebrew dependency and registers
    # the exact alias used by the company's DBL; existing registrations above win.
    script = repos['kicad_library_manager'] / 'scripts/setup_odbc_macos.sh'
    subprocess.run(['/bin/bash', str(script)], env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--home', type=Path, default=Path.home(), help=argparse.SUPPRESS)
    parser.add_argument('--no-clone', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--skip-database', action='store_true', help='Skip the one-time macOS SQLite ODBC setup')
    args = parser.parse_args()
    try:
        cli = args.cli or find_cli(sys.platform, args.home)
        if run([str(cli), 'version'], timeout=10).split('.')[0] != '10':
            raise ValueError('The selected CLI must belong to KiCad 10.')
        python = args.python or find_python(cli)
        if args.dry_run:
            print(json.dumps({'cli': str(cli), 'python': str(python),
                              'directories': {k: str(v) for k,v in directories(args.home, sys.platform).items()}}, indent=2))
            return 0
        repos = checkouts(ROOT, clone=not args.no_clone)
        result = install(repos, args.home, sys.platform, python)
        if sys.platform == 'darwin' and not args.skip_database:
            setup_mac_database(repos)
        print('Installed the KiCad 10 trial. KiCad 9 settings and plugin links were left untouched.')
        print(json.dumps(result, indent=2))
        print('Open PCB Editor and allow KiCad to install each plugin dependency environment on first launch.')
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
