"""Small, per-user settings store; never stored inside a PCB or plugin checkout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile


_RUNTIME_MAJOR = 9


def configure_runtime(major: int) -> None:
    """Keep KiCad 10 settings, backups and instance lock separate from KiCad 9."""
    from .compat import SUPPORTED_MAJORS
    if major not in SUPPORTED_MAJORS:
        raise ValueError('Choose KiCad 9 or 10.')
    global _RUNTIME_MAJOR
    _RUNTIME_MAJOR = major


def kicad_user_template_directory(environ: dict | None = None, platform: str | None = None,
                                  major: int = 9) -> Path:
    """The connected KiCad major's template folder, honoring explicit path overrides."""
    from .compat import SUPPORTED_MAJORS
    if major not in SUPPORTED_MAJORS:
        raise ValueError('Choose KiCad 9 or 10.')
    env = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    override = str(env.get('KICAD_USER_TEMPLATE_DIR', '')).strip()
    if override:
        return Path(override).expanduser()
    version = f'{major}.0'
    if env.get('KICAD_DOCUMENTS_HOME'):
        app = 'kicad' if system == 'linux' else 'KiCad'
        return Path(env['KICAD_DOCUMENTS_HOME']).expanduser() / app / version / 'template'
    if system == 'win32':
        home = Path(env.get('USERPROFILE') or Path.home())
        return home / 'Documents' / 'KiCad' / version / 'template'
    if system == 'darwin':
        home = Path(env.get('HOME') or Path.home())
        return home / 'Documents' / 'KiCad' / version / 'template'
    data = str(env.get('XDG_DATA_HOME', '')).strip()
    base = Path(data).expanduser() if data else Path(env.get('HOME') or Path.home()) / '.local' / 'share'
    return base / 'kicad' / version / 'template'


def data_directory() -> Path:
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    directory = base / 'kicad_project_manager'
    return directory / '10.0' if _RUNTIME_MAJOR == 10 else directory


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
