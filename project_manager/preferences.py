"""Small, per-user settings store; never stored inside a PCB or plugin checkout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile


def data_directory() -> Path:
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    return base / 'kicad_project_manager'


def load_template(directory: Path | None = None) -> str:
    try:
        value = json.loads(((directory or data_directory()) / 'settings.json').read_text('utf-8'))
        return value.get('template_directory', '') if isinstance(value.get('template_directory', ''), str) else ''
    except (OSError, ValueError, AttributeError):
        return ''


def save_template(value: str, directory: Path | None = None) -> None:
    folder = directory or data_directory()
    folder.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='settings-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'template_directory': value}, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temp, folder / 'settings.json')
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
