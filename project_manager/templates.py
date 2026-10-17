"""Versioned local templates, explicit ownership, and guarded project migrations.

No board, schematic, project settings, Git checkout, or installed system font is
changed here. Published manifests cover an explicit inventory of automation files.
"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .setup import CONFIG, METADATA, WORKFLOW, Change, commit_changes, read_file, project_path, workflow_block
from .yaml_fields import ScalarBlock, SetupError

MANIFEST = 'kibot_resources/project_manager/template.json'
STATE = 'kibot_resources/project_manager/project.json'
MAX_SIZE = 32 * 1024 * 1024
DOCUMENTS = {f'kibot_resources/templates/{name}.txt' for name in
             ('fabrication_notes', 'assembly_notes', 'impedance_table', 'readme')}
VERSION = r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'


class TemplateSourceError(SetupError):
    """A short overview message with actionable file details kept separately."""
    def __init__(self, summary: str, details: str):
        self.summary = summary
        self.details = details
        super().__init__(summary + '\n\n' + details)


def version_tuple(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(VERSION, value):
        raise SetupError('Template versions must use MAJOR.MINOR.PATCH (no leading zeroes).')
    return tuple(map(int, value.split('.')))


def policy(path: str) -> str:
    """The public plugin controls allowed locations and ownership, not arbitrary JSON."""
    if (not isinstance(path, str) or '\\' in path or ':' in path
            or str(PurePosixPath(path)) != path or path.startswith('/')
            or any(p in ('..', '.', '.git') for p in PurePosixPath(path).parts)):
        raise SetupError('Invalid template file path.')
    if path == CONFIG:
        return 'metadata'
    if path == WORKFLOW:
        return 'workflow'
    if path == 'kibot_yaml/kibot_pre_set_text_variables.yaml':
        return 'release_variables'
    if path in DOCUMENTS:
        return 'document'
    if path == 'kibot_launch.sh':
        return 'text'
    if path.startswith('kibot_yaml/') and path.endswith(('.yaml', '.yml')):
        return 'text'
    if path.startswith('Templates/') and path.endswith('.kicad_wks'):
        return 'text'
    for folder in ('fonts', 'colors', 'scripts', 'templates'):
        if path.startswith(f'kibot_resources/{folder}/'):
            return 'binary' if folder == 'fonts' else 'text'
    raise SetupError(f'This location cannot be managed by a template: {path}')


def inventory(root: Path) -> list[str]:
    paths = []
    for folder in ('kibot_yaml', 'Templates', 'kibot_resources/fonts', 'kibot_resources/colors',
                   'kibot_resources/scripts', 'kibot_resources/templates'):
        parent = project_path(root, folder)
        if not parent.exists():
            continue
        for item in parent.rglob('*'):
            relative = item.relative_to(root).as_posix()
            if '__pycache__' in item.parts or item.name.startswith('.') or item.suffix == '.pyc':
                continue
            if item.is_symlink():
                raise SetupError(f'{relative} uses a symbolic link.')
            if item.is_file():
                try:
                    policy(relative)
                except SetupError:
                    continue
                paths.append(relative)
    for path in (WORKFLOW, 'kibot_launch.sh'):
        if project_path(root, path).exists():
            paths.append(path)
    return sorted(paths)


def text(data: bytes) -> str:
    try:
        return data.decode('utf-8-sig').replace('\r\n', '\n')
    except UnicodeError as exc:
        raise SetupError('Managed text files must use UTF-8 encoding.') from exc


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_hash(path: str, data: bytes) -> str:
    # Git's platform newline conversion must not invalidate an installed release.
    return digest(data if policy(path) == 'binary' else text(data).encode('utf-8'))


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def decode_json(data: bytes, label: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SetupError(f'{label} contains a duplicate key: {key}')
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
    except SetupError:
        raise
    except (ValueError, UnicodeError) as exc:
        raise SetupError(f'{label} is not valid JSON.') from exc
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise SetupError(f'{label} has an unsupported schema version.')
    if not isinstance(value.get('template_id'), str) or not value['template_id'].strip():
        raise SetupError(f'{label} is missing its template identity.')
    version_tuple(value.get('template_version'))
    return value


def _mask(data: bytes, block: str, keys: tuple[str, ...]) -> str:
    parsed = ScalarBlock(data, block, keys)
    value = parsed.text
    for field in sorted(parsed.fields.values(), key=lambda f: f.start, reverse=True):
        value = value[:field.start] + "'KPM_PROJECT_VALUE'" + value[field.end:]
    return value


def release_entries(value: str) -> tuple[str, str]:
    """Extract only numbered changelog entries with the established command shape.

    UNRELEASED and commands/definitions elsewhere remain company-controlled.
    An unfamiliar entry is left in place so it is reported as a modification.
    """
    pattern = re.compile(
        r"^    - variable: (?P<vq>['\"]?)@RELEASE_(?P<kind>TITLE|BODY)_VAR@"
        r"(?P<version>\d+\.\d+\.\d+)(?P=vq)[ \t]*\n"
        r"      command: (?P<cq>['\"]?)@GET_(?P=kind)_CMD@ (?P=version)(?P=cq)[ \t]*(?:\n|$)", re.M)
    entries = []
    seen = set()
    def take(match):
        identity = (match['kind'], match['version'])
        if identity in seen:
            raise SetupError('Duplicate changelog text variable; resolve it before upgrading.')
        seen.add(identity)
        entries.append(match[0].rstrip('\n') + '\n')
        return ''
    remaining = pattern.sub(take, value)
    return remaining, ''.join(entries)


def shared_yaml_lines(value: str) -> str:
    """Ignore YAML-only comments without ignoring executable block-scalar content."""
    lines = []
    block_indent = None
    for line in value.splitlines():
        indent = len(line) - len(line.lstrip(' '))
        if block_indent is not None:
            if not line.strip() or indent > block_indent:
                lines.append(line)
                continue
            block_indent = None
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        lines.append(line.rstrip())
        if re.search(r':\s*[|>](?:[+-][1-9]?|[1-9][+-]?)?\s*(?:#.*)?$', line):
            block_indent = indent
    return '\n'.join(lines)


def canonical(path: str, data: bytes) -> bytes:
    kind = policy(path)
    if kind == 'binary':
        return data
    value = text(data)
    if kind == 'metadata':
        value = _mask(value.encode(), 'definitions', METADATA)
    elif kind == 'workflow':
        workflow_block(value.encode())  # Check compatibility, not only the editable status.
        value = _mask(value.encode(), 'env', ('kibot_variant',))
    elif kind == 'release_variables':
        value, _ = release_entries(value)
    if kind in ('metadata', 'workflow', 'release_variables'):
        # Comments and blank lines may surround project-owned fields/entries.
        value = shared_yaml_lines(value)
    return value.encode('utf-8')


def adapted(path: str, incoming: bytes, current: bytes | None) -> bytes:
    if current is None:
        return incoming
    kind = policy(path)
    if kind == 'metadata':
        values = ScalarBlock(current, 'definitions', METADATA).values
        incoming = ScalarBlock(incoming, 'definitions', METADATA).replace(values)
    elif kind == 'workflow':
        values = workflow_block(current).values
        incoming = workflow_block(incoming).replace({'kibot_variant': values['kibot_variant']})
    elif kind == 'release_variables':
        value, _ = release_entries(text(incoming))
        _, entries = release_entries(text(current))
        if entries:
            marker = re.search(r"^    - variable: ['\"]?@RELEASE_TITLE_VAR@UNRELEASED", value, re.M)
            if not marker:
                raise SetupError('Cannot locate the changelog insertion point in the selected template.')
            value = value[:marker.start()] + entries + '\n' + value[marker.start():]
        incoming = value.encode('utf-8')
    if kind != 'binary':
        # Retain the project's newline convention for replaced text files.
        value = text(incoming)
        incoming = value.replace('\n', '\r\n').encode() if b'\r\n' in current else value.encode()
    return incoming


def merge_document(base: str, current: str, incoming: str) -> bytes | None:
    """Propose a three-way line merge only when edits do not overlap."""
    original = base.splitlines(True)
    edits = []
    for value in (current, incoming):
        lines = value.splitlines(True)
        for tag, first, last, start, end in difflib.SequenceMatcher(None, original, lines, autojunk=False).get_opcodes():
            if tag == 'equal':
                continue
            change = (first, last, lines[start:end])
            if change in edits:
                continue
            for other_first, other_last, _ in edits:
                overlaps = (max(first, other_first) < min(last, other_last)
                            or (first == last and other_first <= first <= other_last)
                            or (other_first == other_last and first <= other_first <= last))
                if overlaps:
                    return None
            edits.append(change)
    result = list(original)
    for first, last, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        result[first:last] = replacement
    return ''.join(result).encode('utf-8')


@dataclass(frozen=True)
class Template:
    root: Path
    manifest: bytes
    identity: str
    version: str
    files: dict[str, bytes]


def build_manifest(root: Path, identity: str, version: str) -> bytes:
    version_tuple(version)
    files = {}
    for relative in inventory(root):
        data = read_file(root, relative, MAX_SIZE)
        canonical(relative, data)
        files[relative] = {'policy': policy(relative), 'sha256': source_hash(relative, data)}
    if CONFIG not in files or WORKFLOW not in files:
        raise SetupError('A template must include KiBot configuration and a compatible workflow.')
    return json_bytes({'schema_version': 1, 'template_id': identity, 'template_version': version, 'files': files})


def load_template(root: Path) -> Template:
    root = root.expanduser().resolve(strict=True)
    raw = read_file(root, MANIFEST, MAX_SIZE)
    if raw is None:
        raise TemplateSourceError('This folder has no template version information.',
                                  f'Missing file: {MANIFEST}\n\nChoose a versioned template, '
                                  'or ask its maintainer to prepare a versioned release.')
    manifest = decode_json(raw, 'Template manifest')
    entries = manifest.get('files')
    if not isinstance(entries, dict) or not 1 <= len(entries) <= 1000:
        raise SetupError('Template manifest must contain 1–1000 managed files.')
    if CONFIG not in entries or WORKFLOW not in entries:
        raise SetupError('Template manifest must include KiBot configuration and its workflow.')
    installed = set(inventory(root))
    if installed != set(entries):
        details = [f'The installed file list no longer matches template version {manifest["template_version"]}.']
        details.extend(f'Missing or renamed file: {path}' for path in sorted(set(entries) - installed))
        details.extend(f'Added or renamed file: {path}' for path in sorted(installed - set(entries)))
        details.append('\nRestore the original template files, or publish these changes as a new template version. '
                       'Project checks are paused until the installed template is ready to use.')
        raise TemplateSourceError('Installed template files were added, removed or renamed.', '\n'.join(details))
    files = {}
    modified = []
    total = 0
    for relative, entry in entries.items():
        kind = policy(relative)
        if not isinstance(entry, dict) or entry.get('policy') != kind:
            raise SetupError(f'Unsupported template ownership rule: {relative}')
        data = read_file(root, relative, MAX_SIZE)
        if data is None or source_hash(relative, data) != entry.get('sha256'):
            modified.append(relative)
            continue
        canonical(relative, data)
        total += len(data)
        if total > 128 * 1024 * 1024:
            raise SetupError('Template exceeds the 128 MB managed-file limit.')
        files[relative] = data
    if modified:
        details = [f'These installed files no longer match template version {manifest["template_version"]}:',
                   *modified, '', 'Restore their original contents, or publish your edits as a new template version. '
                   'Project checks are paused until the installed template is ready to use.']
        raise TemplateSourceError('Installed template modified. Restore its original files, or publish your edits '
                                  'as a new template version before checking this project.',
                                  '\n'.join(details))
    return Template(root, raw, manifest['template_id'], manifest['template_version'], files)


def resolve_template(root: Path, storage: Path | None, user_directory: Path) -> Path | None:
    """Remember machine-local paths by portable identity; discover only unambiguous matches."""
    from .preferences import load_template as saved_template
    root = root.expanduser().resolve(strict=True)
    user_directory = user_directory.expanduser().resolve()
    record = read_file(root, STATE, MAX_SIZE) or read_file(root, MANIFEST, MAX_SIZE)
    identity = decode_json(record, 'Project template record')['template_id'] if record else None
    selected = saved_template(storage, identity)
    if selected:
        return Path(selected).expanduser().resolve()
    if not identity or not user_directory.is_dir():
        return None
    candidates = []
    for candidate in sorted(user_directory.iterdir()):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        if candidate == root or root in candidate.parents or candidate in root.parents:
            continue
        raw = read_file(candidate, MANIFEST, MAX_SIZE)
        if raw:
            try:
                if decode_json(raw, 'Installed template')['template_id'] == identity:
                    candidates.append(candidate)
            except SetupError:
                continue
    return candidates[0] if len(candidates) == 1 else None


@dataclass(frozen=True)
class Item:
    path: str
    status: str
    current: bytes | None
    incoming: bytes
    suggested: str
    base: dict | None
    merged: bytes | None = None

    def diff(self) -> str:
        if policy(self.path) == 'binary':
            return (f'{self.path}\n\nProject SHA256: {digest(self.current) if self.current is not None else "missing"}'
                    f'\nTemplate SHA256: {digest(self.incoming)}\n')
        return ''.join(difflib.unified_diff(text(self.current or b'').splitlines(True),
                       text(self.incoming).splitlines(True), fromfile='Project/' + self.path,
                       tofile='Template/' + self.path)) or 'Contents match (allowed project settings are preserved).'


@dataclass(frozen=True)
class Comparison:
    root: Path
    template: Template
    version: str | None
    manifest_before: bytes | None
    state_before: bytes | None
    items: tuple[Item, ...]
    extras: tuple[str, ...]

    @property
    def problems(self) -> tuple[Item, ...]:
        return tuple(item for item in self.items if item.status not in ('Matches', 'Project content preserved'))

    @property
    def colour(self) -> str:
        if any(item.current is None for item in self.items):
            return 'red'
        if self.problems or (self.version and self.version != self.template.version):
            return 'amber'
        return 'green' if self.version else 'neutral'

    @property
    def summary(self) -> str:
        prefix = f'Project {self.version or "version unknown"} · Installed {self.template.version}'
        if any(item.current is None for item in self.items):
            status = 'Missing required files'
        elif self.version and version_tuple(self.version) < version_tuple(self.template.version):
            status = 'Update available'
        elif self.version and version_tuple(self.version) > version_tuple(self.template.version):
            status = 'Installed template is older'
        elif self.problems:
            status = 'Local modifications / review needed'
        elif not self.version:
            status = 'Matches installed files · Review adoption'
        else:
            status = 'Matches installed template'
        return prefix + ' · ' + status


def compare(root: Path, template: Template) -> Comparison:
    root = root.resolve(strict=True)
    if root == template.root or root in template.root.parents or template.root in root.parents:
        raise SetupError('The project and template must be separate, non-nested folders.')
    manifest_before = read_file(root, MANIFEST, MAX_SIZE)
    state_before = read_file(root, STATE, MAX_SIZE)
    recorded = decode_json(state_before, 'Project template record') if state_before else None
    initial = decode_json(manifest_before, 'Project template manifest') if manifest_before else None
    for record in (recorded, initial):
        if record and record['template_id'] != template.identity:
            raise SetupError('This project belongs to a different template. Choose its matching template source.')
    if (initial and initial['template_version'] == template.version
            and initial.get('files') != decode_json(template.manifest, 'Template manifest').get('files')):
        raise SetupError('The installed template reuses a recorded version for different source contents. '
                         'Publish the changed template with a new version.')
    version = (recorded or initial or {}).get('template_version')
    baselines = (recorded or {}).get('files', {})
    if not isinstance(baselines, dict):
        raise SetupError('Invalid project template baselines.')
    # A manifest copied by KiCad provides identity/version, but does not prove contents.
    items = []
    for relative, data in template.files.items():
        current = read_file(root, relative, MAX_SIZE)
        base = baselines.get(relative)
        if base is not None:
            if (not isinstance(base, dict) or base.get('policy') != policy(relative)
                    or not re.fullmatch(r'[0-9a-f]{64}', str(base.get('hash', '')))):
                raise SetupError(f'Invalid recorded baseline for {relative}.')
            if policy(relative) == 'document' and not isinstance(base.get('content'), str):
                raise SetupError(f'Missing document baseline for {relative}.')
            if policy(relative) == 'document' and digest(base['content'].encode()) != base['hash']:
                raise SetupError(f'Document baseline hash mismatch for {relative}.')
        incoming = adapted(relative, data, current)
        merged = None
        expected_hash = digest(canonical(relative, data))
        current_hash = digest(canonical(relative, current)) if current is not None else None
        if current is None:
            status, action = 'Missing required file', 'use'
        elif current_hash == expected_hash:
            status, action = 'Matches', 'keep'
        elif policy(relative) == 'document':
            if base and base['hash'] == expected_hash:
                status, action = 'Project content preserved', 'keep'
            elif base and current_hash == base['hash']:
                status, action = 'Template changed; project unchanged', 'use'
            else:
                status, action = 'Review project content and incoming template', 'review'
                if base:
                    merged = merge_document(base['content'], text(current), text(data))
                    if merged is not None:
                        status, action = 'Template and project changed; merge proposed', 'edit'
        elif base and current_hash == base['hash']:
            status, action = 'Template changed; project unchanged', 'use'
        else:
            status, action = 'Local modifications; review before replacement', 'review'
        items.append(Item(relative, status, current, incoming, action, base, merged))
    extras = tuple(p for p in inventory(root) if p not in template.files)
    return Comparison(root, template, version, manifest_before, state_before, tuple(items), extras)


@dataclass(frozen=True)
class Upgrade:
    comparison: Comparison
    changes: tuple[Change, ...]

    def diff(self) -> str:
        parts = []
        for change in self.changes:
            if change.relative in (STATE, MANIFEST):
                parts.append(f'{change.relative}: record template {self.comparison.template.version}\n')
            elif policy(change.relative) == 'binary':
                parts.append(f'{change.relative}: replace binary file ({len(change.after)} bytes)\n')
            else:
                parts.append(''.join(difflib.unified_diff(text(change.before or b'').splitlines(True),
                             text(change.after).splitlines(True), fromfile='Project/' + change.relative,
                             tofile='Updated/' + change.relative)))
        return '\n'.join(parts) or 'No file changes are needed.'


def prepare_upgrade(comparison: Comparison, choices: dict[str, str], edits: dict[str, bytes] | None = None) -> Upgrade:
    """Explicit review acknowledges preserved document edits as the new baseline.

    Company-file exceptions remain visible as local modifications after apply.
    """
    if set(choices) != {item.path for item in comparison.items}:
        raise SetupError('Review every managed file before applying the template.')
    if comparison.version and version_tuple(comparison.version) > version_tuple(comparison.template.version):
        raise SetupError('Choose a template at least as new as the project; downgrades are not supported.')
    changes, files = [], {}
    for item in comparison.items:
        choice = choices[item.path]
        if choice == 'review':
            raise SetupError(f'Choose whether to keep, replace, or merge this file: {item.path}')
        if choice not in ('keep', 'use', 'edit'):
            raise SetupError('Unknown template review action.')
        if item.current is None and choice == 'keep':
            raise SetupError(f'Required file cannot be left missing: {item.path}')
        edited = (edits or {}).get(item.path, item.merged)
        if choice == 'edit' and (policy(item.path) != 'document' or edited is None):
            raise SetupError('Only project documentation can be edited in template review.')
        after = item.current if choice == 'keep' else (edited if choice == 'edit' else item.incoming)
        if policy(item.path) == 'document' and choice == 'edit' and b'\r\n' in (item.current or b''):
            after = text(after).replace('\n', '\r\n').encode()
        if after != item.current:
            changes.append(Change(item.path, item.current, after))
        data = comparison.template.files[item.path]
        files[item.path] = {'policy': policy(item.path), 'hash': digest(canonical(item.path, data))}
        if policy(item.path) == 'document':
            files[item.path]['content'] = text(data)
    template = comparison.template
    state = json_bytes({'schema_version': 1, 'template_id': template.identity,
                        'template_version': template.version, 'files': files})
    for path, before, after in ((MANIFEST, comparison.manifest_before, template.manifest),
                                (STATE, comparison.state_before, state)):
        if before != after:
            changes.append(Change(path, before, after))
    return Upgrade(comparison, tuple(changes))


def apply_upgrade(plan: Upgrade, storage: Path | None = None, verify=None) -> Path | None:
    comparison = plan.comparison
    def unchanged():
        if verify:
            verify()
        template = load_template(comparison.template.root)
        if template.manifest != comparison.template.manifest or template.files != comparison.template.files:
            raise SetupError('The selected template changed during review. Refresh and review again.')
        for path, original in ((MANIFEST, comparison.manifest_before), (STATE, comparison.state_before),
                               *((item.path, item.current) for item in comparison.items)):
            if read_file(comparison.root, path, MAX_SIZE) != original:
                raise SetupError(f'{path} changed during review. Refresh and review again.')
        if tuple(p for p in inventory(comparison.root) if p not in template.files) != comparison.extras:
            raise SetupError('The project file inventory changed during review. Refresh and review again.')
    return commit_changes(comparison.root, plan.changes, storage, limit=MAX_SIZE, verify=unchanged)
