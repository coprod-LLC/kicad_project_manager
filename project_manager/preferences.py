"""Small, per-user settings store; never stored inside a PCB or plugin checkout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile


def kicad_user_template_directory(environ: dict | None = None, platform: str | None = None) -> Path:
    """KiCad 9's per-user template folder. ``KICAD_USER_TEMPLATE_DIR`` overrides the default."""
    env = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    override = str(env.get('KICAD_USER_TEMPLATE_DIR', '')).strip()
    if override:
        return Path(override).expanduser()
    if system == 'win32':
        home = Path(env.get('USERPROFILE') or Path.home())
        return home / 'Documents' / 'KiCad' / '9.0' / 'template'
    if system == 'darwin':
        home = Path(env.get('HOME') or Path.home())
        return home / 'Documents' / 'KiCad' / '9.0' / 'template'
    data = str(env.get('XDG_DATA_HOME', '')).strip()
    base = Path(data).expanduser() if data else Path(env.get('HOME') or Path.home()) / '.local' / 'share'
    return base / 'kicad' / '9.0' / 'template'


def data_directory() -> Path:
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    return base / 'kicad_project_manager'


def load_settings(directory: Path | None = None, *, strict: bool = False) -> dict:
    try:
        value = json.loads(((directory or data_directory()) / 'settings.json').read_text('utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Settings must contain a JSON object.')
        return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        if strict:
            raise
        return {}


def save_settings(values: dict, directory: Path | None = None) -> None:
    folder = directory or data_directory()
    settings = load_settings(directory, strict=True)
    settings.update(values)
    folder.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='settings-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(settings, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temp, folder / 'settings.json')
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_template(directory: Path | None = None, identity: str | None = None) -> str:
    settings = load_settings(directory)
    sources = settings.get('template_directories', {})
    value = (sources.get(identity, '') if identity and isinstance(sources, dict)
             else settings.get('template_directory', ''))
    return value if isinstance(value, str) else ''


def save_template(value: str, directory: Path | None = None, identity: str | None = None) -> None:
    settings = load_settings(directory, strict=True)
    sources = settings.get('template_directories', {})
    sources = dict(sources) if isinstance(sources, dict) else {}
    if identity:
        sources[identity] = value
    save_settings({'template_directory': value, 'template_directories': sources}, directory)


def load_standards(directory: Path | None = None) -> str:
    value = load_settings(directory).get('standards_directory', '')
    return value if isinstance(value, str) else ''
