"""Refuse a second plugin window, including after a crashed lock is left behind."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from .preferences import data_directory

_CHECKER = None


def _user_key() -> str:
    try:
        return str(os.getuid())
    except AttributeError:
        return str(os.environ.get('USERNAME') or os.environ.get('USER') or 'user')


def _pid_path() -> Path:
    return data_directory() / 'instance_pid.json'


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_pid() -> int | None:
    try:
        payload = json.loads(_pid_path().read_text(encoding='utf-8') or '{}')
        pid = payload.get('pid')
        return int(pid) if isinstance(pid, int) or (isinstance(pid, str) and pid.isdigit()) else None
    except (OSError, ValueError, TypeError):
        return None


def _write_pid() -> None:
    try:
        path = _pid_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as stream:
            json.dump({'pid': os.getpid(), 'exe': sys.executable}, stream)
            stream.write('\n')
    except OSError:
        return


def ensure_single_instance(app) -> bool:
    """Return True when this process may open the window. A second launch explains why and returns False."""
    global _CHECKER
    try:
        import wx
        directory = data_directory()
        directory.mkdir(parents=True, exist_ok=True)
        name = f'kicad_project_manager_single_instance_{_user_key()}'
        lock = directory / name
        _CHECKER = wx.SingleInstanceChecker(name, str(directory))
        if _CHECKER.IsAnotherRunning():
            pid = _read_pid()
            if pid is not None and not _pid_alive(pid):
                for path in (lock, directory / 'instance_pid.json'):
                    try:
                        path.unlink()
                    except OSError:
                        pass
                _CHECKER = wx.SingleInstanceChecker(name, str(directory))
            if _CHECKER.IsAnotherRunning():
                pid = _read_pid()
                hint = f'\n\nDetected running PID: {pid}' if pid else ''
                wx.MessageBox(
                    'KiCad Project Manager is already running.\n\n'
                    'Close the existing window before opening it again.'
                    + hint + '\n\n'
                    'If you cannot find the window, it may be behind KiCad. '
                    'You can close that process and try again.',
                    'KiCad Project Manager', wx.OK | wx.ICON_INFORMATION)
                _CHECKER = None
                if wx.GetApp() is app and not wx.GetTopLevelWindows():
                    app.ExitMainLoop()
                return False
        _write_pid()
        return True
    except Exception:
        return True
