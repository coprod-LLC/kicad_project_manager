"""Project setup plans and guarded writes; no KiCad board/project file writes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit
import uuid

from .preferences import data_directory
from .yaml_fields import ScalarBlock, SetupError

METADATA = ('PROJECT_NAME', 'BOARD_NAME', 'COMPANY', 'DESIGNER', 'REVIEWER', 'GIT_URL', 'LOGO')
STATUSES = ('DRAFT', 'PRELIMINARY', 'CHECKED')
CONFIG = 'kibot_yaml/kibot_main.yaml'
WORKFLOW = '.github/workflows/ci.yaml'
MAX_FILE_SIZE = 2 * 1024 * 1024


def metadata_block(data: bytes) -> ScalarBlock:
    # Older templates have no reviewer. Preserve a no-op until a name is entered.
    return ScalarBlock(data, 'definitions', tuple(key for key in METADATA if key != 'REVIEWER'),
                       optional=('REVIEWER',))


def project_path(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise SetupError('Configuration path must stay inside the project.')
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise SetupError(f'{relative} uses a symbolic link. Edit the real project instead.')
    return path


def read_file(root: Path, relative: str, limit: int = MAX_FILE_SIZE) -> bytes | None:
    path = project_path(root, relative)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > limit:
        raise SetupError(f'{relative} must be a regular configuration file smaller than {max(limit // (1024 * 1024), 1)} MB.')
    return path.read_bytes()


def workflow_block(data: bytes) -> ScalarBlock:
    block = ScalarBlock(data, 'env', ('kibot_variant', 'kibot_config', 'kicad_version'))
    if block.values['kibot_config'].removeprefix('./') != CONFIG:
        raise SetupError(f'This workflow does not use {CONFIG}. Its configuration is unsupported.')
    if block.values['kicad_version'] != '9':
        raise SetupError('This workflow targets a different KiCad version. Select a KiCad 9 template.')
    return block


@dataclass(frozen=True)
class Snapshot:
    root: Path
    config: bytes
    workflow: bytes | None
    metadata: dict[str, str]
    status: str


def read_setup(root: Path) -> Snapshot:
    root = root.resolve(strict=True)
    config = read_file(root, CONFIG)
    if config is None:
        raise SetupError(f'Missing {CONFIG}. Start with a Coprod or KDT KiBot template project.')
    if not re.search(rb'^\.\.\.\r?$', config, re.M):
        raise SetupError('KiBot configuration has no separate definitions document.')
    fields = metadata_block(config).values
    workflow = read_file(root, WORKFLOW)
    status = workflow_block(workflow).values['kibot_variant'] if workflow is not None else 'DRAFT'
    return Snapshot(root, config, workflow, fields, status)


def git_value(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(['git', '-C', str(root), *arguments], capture_output=True,
                                text=True, timeout=3, check=False,
                                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        return result.stdout.strip() if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return ''


def web_url(remote: str) -> str:
    if re.match(r'^[\w.-]+@[\w.-]+:', remote):
        host, path = remote.split('@', 1)[1].split(':', 1)
        return 'https://' + host + '/' + path.removesuffix('.git')
    try:
        parsed = urlsplit(remote)
        if parsed.scheme not in ('https', 'http', 'ssh') or not parsed.hostname:
            return ''
        # Never put credentials, query tokens, or fragments from Git config in YAML.
        host = parsed.hostname
        if ':' in host:
            host = '[' + host + ']'
        if parsed.port and parsed.scheme != 'ssh':
            host += ':' + str(parsed.port)
        return urlunsplit(('https' if parsed.scheme == 'ssh' else parsed.scheme,
                          host, parsed.path.removesuffix('.git'), '', ''))
    except ValueError:
        return ''


def suggested_metadata(snapshot: Snapshot, project_name: str, board_name: str) -> dict[str, str]:
    values = dict(snapshot.metadata)
    suggestions = {'PROJECT_NAME': project_name, 'BOARD_NAME': board_name,
                   'DESIGNER': git_value(snapshot.root, 'config', 'user.name'),
                   'GIT_URL': web_url(git_value(snapshot.root, 'remote', 'get-url', 'origin'))}
    placeholders = {'PROJECT_NAME': ('', 'Project Name'), 'BOARD_NAME': ('', 'Board Name'),
                    'DESIGNER': ('', 'Author'), 'GIT_URL': ('', 'https://github.com/nguyen-v/KDT_Hierarchical_KiBot')}
    for key, value in suggestions.items():
        current = values[key]
        if value and (current in placeholders[key] or (key == 'GIT_URL' and '[PROJECT NAME]' in current)):
            values[key] = value
    return values


def validate_metadata(values: dict[str, str], previous: dict[str, str]) -> None:
    if set(values) != set(METADATA):
        raise SetupError('The project metadata fields are incomplete.')
    for key, value in values.items():
        if not isinstance(value, str) or any(ord(c) < 32 for c in value):
            raise SetupError(f'{key} must be single-line text.')
        if key in ('PROJECT_NAME', 'BOARD_NAME', 'DESIGNER') and not value.strip():
            raise SetupError('Project name, board name, and at least one author are required.')
        if value == previous[key]:
            continue
        # KiBot performs literal substitution into plain AND single-quoted YAML.
        # A quoted definitions value alone cannot make these template uses safe.
        unsafe = ("'" in value or re.search(r':(?:\s|$)|(?:^|\s)#|@[\w]+@', value)
                  or re.match(r'^[\[\]{}&*!|>\"%@`]|^[-?:](?:\s|$)', value)
                  or re.fullmatch(r'(?i:true|false|yes|no|on|off|null|~|[-+]?(?:\d[\d_.:e+-]*|\.\d+|\.inf|\.nan)|0x[\da-f]+|\d{4}-\d\d-\d\d)', value))
        if unsafe:
            raise SetupError(f'{key}: this template substitutes text directly into YAML. '
                             'Quotes, YAML expressions, and colon-space or comment sequences '
                             'need template changes before they can be used safely.')


@dataclass(frozen=True)
class Change:
    relative: str
    before: bytes | None
    after: bytes


@dataclass(frozen=True)
class Plan:
    snapshot: Snapshot
    changes: tuple[Change, ...]

    def diff(self) -> str:
        return '\n'.join(''.join(difflib.unified_diff(
            (c.before or b'').decode('utf-8').splitlines(keepends=True),
            c.after.decode('utf-8').splitlines(keepends=True),
            fromfile=c.relative if c.before is not None else '/dev/null', tofile=c.relative,
        )) for c in self.changes)


def prepare(snapshot: Snapshot, metadata: dict[str, str], status: str) -> Plan:
    validate_metadata(metadata, snapshot.metadata)
    changes = []
    config = metadata_block(snapshot.config).replace(metadata, prefer_plain=('REVIEWER',))
    if config != snapshot.config:
        changes.append(Change(CONFIG, snapshot.config, config))
    workflow = snapshot.workflow
    if workflow is not None:
        block = workflow_block(workflow)
        if status not in STATUSES and status != block.values['kibot_variant']:
            raise SetupError('Choose DRAFT, PRELIMINARY, or CHECKED. RELEASED is selected by version tags.')
        updated = block.replace({'kibot_variant': status})
        if updated != snapshot.workflow:
            changes.append(Change(WORKFLOW, snapshot.workflow, updated))
    return Plan(snapshot, tuple(changes))


def assert_unchanged(snapshot: Snapshot) -> None:
    for relative, original in ((CONFIG, snapshot.config), (WORKFLOW, snapshot.workflow)):
        if read_file(snapshot.root, relative) != original:
            raise SetupError(f'{relative} changed since it was loaded. Refresh and review the values again.')


def _replace(path: Path, data: bytes, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix='.kpm-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def commit_changes(root: Path, changes: tuple[Change, ...], storage: Path | None = None, *,
                   limit: int = MAX_FILE_SIZE, verify=None) -> Path | None:
    """Back up, write, and roll back a set of project files. ``verify`` re-reads them first."""
    verify = verify or (lambda: None)
    if not changes:
        verify()
        return None
    folder = (storage or data_directory()) / 'backups' / hashlib.sha256(str(root).encode()).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / 'apply.lock'
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SetupError(f'Another apply operation holds {lock}. If it crashed, remove this lock after closing the plugin.') from exc
    os.close(fd)
    written = []
    created_directories = []
    backup = None
    modes = {}
    try:
        verify()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        backup = folder / stamp
        backup.mkdir()
        manifest = {'project': str(root), 'files': []}
        for change in changes:
            path = project_path(root, change.relative)
            modes[change.relative] = path.stat().st_mode & 0o777 if path.exists() else 0o644
            if change.before is not None:
                destination = backup / change.relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(change.before)
            manifest['files'].append({'path': change.relative, 'existed': change.before is not None,
                                      'after_sha256': hashlib.sha256(change.after).hexdigest()})
        (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        verify()
        for change in changes:
            path = project_path(root, change.relative)
            if read_file(root, change.relative, limit) != change.before:
                raise SetupError(f'{change.relative} changed during apply.')
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                parent.mkdir()
                created_directories.append(parent)
            _replace(path, change.after, modes[change.relative])
            written.append(change)
        for change in written:
            if read_file(root, change.relative, limit) != change.after:
                raise SetupError(f'{change.relative} changed during verification.')
        return backup
    except Exception as exc:
        recovery_errors = []
        for change in reversed(written):
            try:
                path = project_path(root, change.relative)
                if read_file(root, change.relative, limit) != change.after:
                    raise SetupError('An external edit prevents automatic restoration.')
                if change.before is None:
                    path.unlink()
                else:
                    _replace(path, change.before, modes[change.relative])
            except Exception:
                recovery_errors.append(change.relative)
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        if recovery_errors:
            raise SetupError(f'Apply failed. Restore {", ".join(recovery_errors)} from {backup}.') from exc
        if isinstance(exc, SetupError):
            raise
        raise SetupError(f'Could not save the project files; original files were retained. Backup: {backup or "not created"}. {exc}') from exc
    finally:
        lock.unlink()


def apply(plan: Plan, storage: Path | None = None) -> Path | None:
    return commit_changes(plan.snapshot.root, plan.changes, storage,
                          verify=lambda: assert_unchanged(plan.snapshot))
