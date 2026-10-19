"""Apply standards by editing saved files. Project rules outside the company block are kept."""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re

from .json_text import JsonTextError, merge_named_list, update_object
from .setup import Change, SetupError, commit_changes, git_value
from .sexpr import ExpressionError, atoms, canonical, forms, spans, tokens
from .standards import Catalog, Preset, StandardsError, _board_major, _constraints, _netclasses

PROVENANCE = 'kpm-standards.json'
COMPANY_BEGIN = '# kicad_project_manager: begin company rules'
COMPANY_END = '# kicad_project_manager: end company rules'
COMPANY_NOTE = '# Project rules outside this block are kept. Later rules override earlier ones.'
FILE_LIMIT = 128 * 1024 * 1024
_SKIP_DIRS = {'.git', 'kibot_out', '__pycache__', '.history'}


class EditorsOpenError(StandardsError):
    """Applying is blocked until the project's KiCad editors are closed."""


@dataclass(frozen=True)
class StandardsPlan:
    root: Path
    changes: tuple[Change, ...]
    summary: str
    missing_dependencies: tuple[str, ...] = ()
    adopts_existing_rules: bool = False

    def diff(self) -> str:
        parts = []
        for change in self.changes:
            before = (change.before or b'').decode('utf-8', 'replace')
            after = change.after.decode('utf-8', 'replace')
            parts.append(''.join(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=change.relative if change.before is not None else '/dev/null',
                tofile=change.relative)))
        return '\n'.join(part for part in parts if part)


def editor_locks(root: Path) -> list[Path]:
    found = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in _SKIP_DIRS]
        for name in files:
            if name.startswith('~') and name.endswith('.lck') and '.kicad_' in name:
                found.append(Path(current) / name)
    return sorted(found)


def editor_lock_message(root: Path) -> str:
    locks = editor_locks(root)
    if not locks:
        return ''
    names = ', '.join(path.name for path in locks[:5])
    extra = '' if len(locks) <= 5 else f' and {len(locks) - 5} more'
    return ('KiCad still has this project open (' + names + extra + '). '
            'Save your work, close the PCB and schematic editors, and leave this window open. '
            'Then apply again and reopen the project. '
            'An open editor keeps the old settings in memory and will overwrite this apply if you save it.')


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise StandardsError(f'{path.name} is outside the project directory.') from exc
    text = relative.as_posix()
    if text.startswith('..'):
        raise StandardsError(f'{path.name} is outside the project directory.')
    return text


def _read_bytes(path: Path, limit: int) -> bytes | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise StandardsError(f'{path.name} must be a regular file smaller than {max(limit // (1024 * 1024), 1)} MB.')
    return path.read_bytes()


def _decode(data: bytes, label: str) -> tuple[str, bool]:
    try:
        return data.decode('utf-8-sig'), data.startswith(b'\xef\xbb\xbf')
    except UnicodeError as exc:
        raise StandardsError(f'{label} must be UTF-8.') from exc


def _encode(text: str, bom: bool) -> bytes:
    data = text.encode('utf-8')
    return b'\xef\xbb\xbf' + data if bom else data


def _rule_names(text: str) -> list[str]:
    names = []
    for name, node in forms(text):
        if name != 'rule':
            continue
        values = atoms(node)
        if len(values) < 2 or not isinstance(values[1], str):
            raise StandardsError('A custom rule is missing its name.')
        names.append(values[1])
    return names


def _rules_body(text: str) -> str:
    found = list(spans(text))
    if not found or found[0][0] != 'version' or atoms(text[found[0][1]:found[0][2]]) != ['version', '1']:
        raise StandardsError('Custom rules must start with (version 1).')
    body = text[found[0][2]:]
    if body.startswith('\r\n'):
        body = body[2:]
    elif body.startswith('\n'):
        body = body[1:]
    return body.strip() + '\n'


def _company_block(preset: Preset, commit: str, dirty: bool) -> str:
    digest = hashlib.sha256(preset.files['custom_rules_file'].encode()).hexdigest()
    lines = [COMPANY_BEGIN, COMPANY_NOTE, f'# preset: {preset.id}', f'# source_sha256: {digest}']
    if commit:
        lines.append(f'# source_commit: {commit}')
    if dirty:
        lines.append('# source_dirty: true')
    lines.append(_rules_body(preset.files['custom_rules_file']).rstrip('\n'))
    lines.append(COMPANY_END)
    return '\n'.join(lines) + '\n'


def _marker_spans(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    begins, ends, offset = [], [], 0
    for line in text.splitlines(keepends=True):
        if line.strip() == COMPANY_BEGIN:
            begins.append((offset, offset + len(line)))
        elif line.strip() == COMPANY_END:
            ends.append((offset, offset + len(line)))
        offset += len(line)
    return begins, ends


def merge_custom_rules(text: str, preset: Preset, *, commit: str = '', dirty: bool = False,
                       allow_rule_adoption: bool = False) -> str:
    """Replace the one company-rules block. Text outside that block is copied unchanged."""
    block = _company_block(preset, commit, dirty)
    if '\r\n' in text:
        block = block.replace('\n', '\r\n')
    preset_names = _rule_names(preset.files['custom_rules_file'])
    if len(preset_names) != len(set(preset_names)):
        raise StandardsError('The preset contains more than one rule with the same name.')
    if not text.strip():
        return '(version 1)\n' + block
    try:
        found = list(spans(text))
    except ExpressionError as exc:
        raise StandardsError(f'Cannot read the custom-rules file: {exc}') from exc
    if not found or found[0][0] != 'version':
        raise StandardsError('Custom rules must start with (version 1).')
    begins, ends = _marker_spans(text)
    if len(begins) > 1 or len(ends) > 1 or bool(begins) != bool(ends):
        raise StandardsError('The custom-rules file has broken company-rules markers. Keep one begin line and one end line.')
    if begins and ends and begins[0][0] > ends[0][0]:
        raise StandardsError('The company-rules end marker is above the begin marker.')
    if begins and ends:
        outside = text[:begins[0][0]] + '\n' + text[ends[0][1]:]
        collision = sorted(set(preset_names) & set(_rule_names(outside)))
        if collision:
            raise StandardsError('These rule names are already used outside the company-rules block: '
                                 + ', '.join(collision) + '. Rename those project rules before applying.')
        return text[:begins[0][0]] + block + text[ends[0][1]:]
    collision = sorted(set(preset_names) & set(_rule_names(text)))
    if collision and COMPANY_NOTE not in text:
        existing = list(found[1:])
        incoming = [node for name, node in forms(preset.files['custom_rules_file']) if name == 'rule']
        prefix = existing[:len(incoming)]
        # Keep rule order. Changed values require an explicitly requested adoption
        # proposal, which the UI always routes through the exact diff review.
        if len(prefix) == len(incoming) and all(name == 'rule' and atoms(text[start:end])[1] == atoms(node)[1]
                                               and (allow_rule_adoption or canonical(text[start:end]) == canonical(node))
                                               for (name, start, end), node in zip(prefix, incoming)):
            return text[:prefix[0][1]] + block + text[prefix[-1][2]:]
    if collision and set(collision) == set(preset_names):
        raise StandardsError('These company rules are already in the file, but the begin/end markers are missing. '
                             'Restore the markers before applying so the project rules are not duplicated.')
    if collision:
        raise StandardsError('These rule names are already used by the project: '
                             + ', '.join(collision) + '. Rename those project rules before applying.')
    insert_at = found[0][2]
    if text.startswith('\r\n', insert_at):
        insert_at += 2
    elif text.startswith('\n', insert_at):
        insert_at += 1
    return text[:insert_at] + block + text[insert_at:]


def _indent_at(text: str, index: int) -> str:
    line = text.rfind('\n', 0, index) + 1
    return text[line:index] if text[line:index].strip() == '' else ''


def _child_indent(form: str) -> str:
    for _name, start, _end in spans(form, children=True):
        indent = _indent_at(form, start)
        if indent:
            return indent
    return '\t'


def _copper_names(layers_form: str) -> list[str]:
    names = []
    for _name, node in forms(layers_form, children=True):
        values = atoms(node)
        if len(values) > 1 and str(values[1]).endswith('.Cu'):
            names.append(str(values[1]))
    return names


def _raw_from_atom(node: str, index: int) -> str:
    depth, seen, start = 0, 0, None
    for _token, left, _right in tokens(node):
        if _token == '(':
            depth += 1
        elif _token == ')':
            if depth == 1 and start is not None:
                return node[start:left].strip()
            depth -= 1
        elif depth == 1:
            if seen == index and start is None:
                start = left
            seen += 1
    return ''


def _with_user_name(preset_node: str, old_node: str | None) -> str:
    preset_node = preset_node.strip()
    if old_node is None:
        return preset_node
    # Layer function and display name belong to the project, not its manufacturer.
    left, _right, _token = _atom_bounds(preset_node, 2)
    return preset_node[:left] + _raw_from_atom(old_node, 2) + ')'


def _merge_layers(layers_form: str, preset_form: str) -> str:
    children = list(spans(layers_form, children=True))
    indexes = [i for i, (_name, start, end) in enumerate(children)
               if len(atoms(layers_form[start:end])) > 1 and str(atoms(layers_form[start:end])[1]).endswith('.Cu')]
    if not indexes:
        raise StandardsError('The board has no copper layers to update.')
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise StandardsError('Copper layers must be grouped together in the board layer list.')
    old = {atoms(layers_form[start:end])[1]: layers_form[start:end] for i, (_name, start, end) in enumerate(children) if i in indexes}
    preset_nodes = [preset_form[start:end] for _name, start, end in spans(preset_form, children=True)]
    lines = [_with_user_name(node, old.get(atoms(node)[1])) for node in preset_nodes]
    start = children[indexes[0]][1]
    end = children[indexes[-1]][2]
    if len(indexes) > 1:
        separator = layers_form[children[indexes[0]][2]:children[indexes[1]][1]]
        if separator.strip():
            separator = '\n' + _indent_at(layers_form, start)
    else:
        separator = '\n' + (_indent_at(layers_form, start) or '\t')
    merged = layers_form[:start] + separator.join(lines) + layers_form[end:]
    return layers_form if canonical(merged) == canonical(layers_form) else merged


def _layer_name(node: str) -> str:
    values = atoms(node)
    return str(values[1]) if len(values) > 1 else ''


def merge_stackup_form(project: str, preset: str) -> str:
    """Replace copper, dielectric, and preset-owned stackup entries. Mask and silk stay unless included."""
    project_children = [(name, project[start:end]) for name, start, end in spans(project, children=True)]
    preset_children = [(name, preset[start:end].strip()) for name, start, end in spans(preset, children=True)]
    preset_layers = [text for name, text in preset_children if name == 'layer']
    preset_names = {_layer_name(text) for text in preset_layers}
    preset_other = {name: text for name, text in preset_children if name != 'layer'}

    def managed(name: str, text: str) -> bool:
        title = _layer_name(text)
        return name == 'layer' and (title.endswith('.Cu') or title.startswith('dielectric'))

    indexes = [i for i, (name, text) in enumerate(project_children) if managed(name, text)]
    if indexes and indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise StandardsError('Copper and dielectric layers must be grouped together in the physical stackup.')
    pieces, inserted, seen_other = [], False, set()
    managed_range = range(indexes[0], indexes[-1] + 1) if indexes else range(0)
    for i, (name, text) in enumerate(project_children):
        if i in managed_range:
            if not inserted:
                pieces.extend(preset_layers)
                inserted = True
            continue
        if name == 'layer' and _layer_name(text) in preset_names:
            continue
        if name != 'layer' and name in preset_other:
            seen_other.add(name)
            pieces.append(preset_other[name])
            continue
        if name != 'layer':
            seen_other.add(name)
        pieces.append(text.strip())
    if not inserted:
        spot = next((i for i, piece in enumerate(pieces) if _layer_name(piece).startswith('B.')), len(pieces))
        pieces[spot:spot] = preset_layers
    for name, text in preset_other.items():
        if name not in seen_other:
            pieces.append(text)
    indent = _child_indent(project)
    close = indent[:-1] if indent else ''
    merged = '(stackup\n' + indent + ('\n' + indent).join(pieces) + '\n' + close + ')' if pieces else '(stackup)'
    return project if canonical(merged) == canonical(project) else merged


def _atom_bounds(node: str, index: int) -> tuple[int, int, str]:
    depth, seen = 0, 0
    for token, left, right in tokens(node):
        if token == '(':
            depth += 1
        elif token == ')':
            depth -= 1
        elif depth == 1:
            if seen == index:
                return left, right, token
            seen += 1
    raise StandardsError('The board thickness entry is incomplete.')


def _number_literal(token: str, new_value) -> str | None:
    try:
        if math.isclose(float(token), float(new_value), rel_tol=0, abs_tol=1e-9):
            return None
    except (TypeError, ValueError):
        pass
    text = f'{float(new_value):.8f}'.rstrip('0').rstrip('.')
    return text or '0'


def _merge_thickness(general: str, new_value) -> str:
    found = [item for item in spans(general, children=True) if item[0] == 'thickness']
    if len(found) != 1:
        raise StandardsError('The board has no single general thickness to update.')
    _name, start, end = found[0]
    left, right, token = _atom_bounds(general[start:end], 1)
    literal = _number_literal(token, new_value)
    if literal is None:
        return general
    return general[:start + left] + literal + general[start + right:]


def _layer_used(text: str, ignored: list[tuple[int, int]], name: str) -> bool:
    chunks, last = [], 0
    for start, end in sorted(ignored):
        chunks.append(text[last:start])
        last = end
    chunks.append(text[last:])
    haystack = '\n'.join(chunks)
    if f'"{name}"' in haystack:
        return True
    return re.search(r'(?<![\w.])' + re.escape(name) + r'(?![\w.])', haystack) is not None


def _only(items: list[tuple[str, int, int]], name: str) -> tuple[str, int, int]:
    found = [item for item in items if item[0] == name]
    if len(found) != 1:
        raise StandardsError(f'Expected exactly one {name} expression.')
    return found[0]


def merge_board(text: str, stackup: Preset) -> tuple[str, list[str], list[str], float]:
    if not re.match(r'\s*\(kicad_pcb(?:\s|\))', text):
        raise StandardsError('Expected a KiCad board file.')
    top = list(spans(text, children=True))
    major = _board_major(text)
    if major not in stackup.details.get('compatible_kicad_majors', [9]):
        raise StandardsError(f'This stackup has not been qualified for KiCad {major}.')
    _name, layers_start, layers_end = _only(top, 'layers')
    _name, general_start, general_end = _only(top, 'general')
    _name, setup_start, setup_end = _only(top, 'setup')
    layers_form = text[layers_start:layers_end]
    old_names = _copper_names(layers_form)
    new_names = _copper_names(stackup.files['copper_layers_file'])
    declared = stackup.details.get('copper_layer_count')
    if type(declared) is int and declared != len(new_names):
        raise StandardsError('Catalog copper_layer_count disagrees with the stackup files.')
    setup = text[setup_start:setup_end]
    stackups = [item for item in spans(setup, children=True) if item[0] == 'stackup']
    if len(stackups) > 1:
        raise StandardsError('Multiple physical stackups found.')
    ignored = [(layers_start, layers_end)]
    if stackups:
        ignored.append((setup_start + stackups[0][1], setup_start + stackups[0][2]))
    used = [name for name in old_names if name not in new_names and _layer_used(text, ignored, name)]
    if used:
        raise StandardsError('Cannot remove copper layer ' + ', '.join(used)
                             + ' because the board still uses it.')
    new_layers = _merge_layers(layers_form, stackup.files['copper_layers_file'])
    original_general = text[general_start:general_end]
    thickness_nodes = [node for name, node in forms(original_general, children=True) if name == 'thickness']
    if len(thickness_nodes) != 1:
        raise StandardsError('The board has no single general thickness to update.')
    current_thickness = float(atoms(thickness_nodes[0])[1])
    ops = []
    if new_layers != layers_form:
        ops.append((layers_start, layers_end, new_layers))
    if stackups:
        current = setup[stackups[0][1]:stackups[0][2]]
        merged = merge_stackup_form(current, stackup.files['stackup_file'])
        if merged != current:
            ops.append((setup_start + stackups[0][1], setup_start + stackups[0][2], merged))
    else:
        indent = _indent_at(text, setup_start) + '\t'
        close = _indent_at(text, setup_end - 1)
        body = stackup.files['stackup_file'].strip()
        merged = body
        ops.append((setup_end - 1, setup_end - 1, '\n' + indent + body + '\n' + close))
    from .stackup import parse_stackup
    # Legacy presets may omit masks; include retained physical layers in the total.
    general = _merge_thickness(original_general, parse_stackup(merged).total_mm)
    if general != original_general:
        ops.append((general_start, general_end, general))
    for start, end, replacement in sorted(ops, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, old_names, new_names, current_thickness


def _areas_and_nets(board: str) -> tuple[set[str], set[str]]:
    nets, areas = set(), set()
    for name, node in forms(board, children=True):
        if name == 'net':
            values = atoms(node)
            if len(values) >= 3:
                nets.add(str(values[2]))
        elif name in ('zone', 'footprint'):
            zones = [node] if name == 'zone' else [child for child_name, child in forms(node, children=True) if child_name == 'zone']
            for zone in zones:
                for field_name, field in forms(zone, children=True):
                    if field_name == 'name' and len(atoms(field)) > 1:
                        areas.add(str(atoms(field)[1]))
    # KiCad 10 stores net names on connected items, without the old root netcodes.
    stack = []
    for token, _left, _right in tokens(board):
        if token == '(':
            stack.append([])
        elif token == ')':
            fields = stack.pop()
            if fields and fields[0] == 'net' and len(fields) == 2 and fields[1].startswith('"'):
                nets.add(json.loads(fields[1]))
        elif stack:
            stack[-1].append(token)
    return areas, nets


def _source_identity(folder: Path | None) -> tuple[str, bool]:
    if folder is None:
        return '', False
    commit = git_value(folder, 'rev-parse', 'HEAD')
    if not re.fullmatch(r'[0-9a-fA-F]{7,64}', commit or ''):
        return '', False
    return commit, bool(git_value(folder, 'status', '--porcelain'))


def _entry(preset: Preset, catalog_name: str, commit: str, dirty: bool) -> dict:
    entry = {'preset_id': preset.id, 'preset_name': preset.name,
             'files': {key: hashlib.sha256(value.encode()).hexdigest() for key, value in sorted(preset.files.items())}}
    if catalog_name:
        entry['catalog'] = catalog_name
    if preset.details.get('preset_file'):
        entry['preset_file'] = preset.details['preset_file']
    if commit:
        entry['source_commit'] = commit
    if dirty:
        entry['source_dirty'] = True
    if preset.details.get('applied_overrides'):
        entry['appearance_overrides'] = preset.details['applied_overrides']
        entry['applied_file_hashes'] = entry['files']
        entry['files'] = preset.details['source_file_hashes']
    return entry


def _merge_provenance(existing: str | None, rules: Preset | None, stackup: Preset | None,
                      catalog_name: str, commit: str, dirty: bool) -> str:
    if existing and existing.strip():
        try:
            data = json.loads(existing)
        except ValueError as exc:
            raise StandardsError(f'{PROVENANCE} is not valid JSON.') from exc
        if not isinstance(data, dict):
            raise StandardsError(f'{PROVENANCE} must be a JSON object.')
        if data.get('schema_version', 1) != 1:
            raise StandardsError(f'{PROVENANCE} uses an unsupported schema_version.')
    else:
        data = {'schema_version': 1}
    data['schema_version'] = 1
    if rules is not None:
        data['rules'] = _entry(rules, catalog_name, commit, dirty)
    if stackup is not None:
        data['stackup'] = _entry(stackup, catalog_name, commit, dirty)
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


def _merge_project(text: str, rules: Preset) -> str:
    try:
        updated = update_object(text, ('board', 'design_settings', 'rules'), _constraints(rules.files['constraints_file']))
        if 'netclass_definitions_file' in rules.files:
            updated = merge_named_list(updated, ('net_settings', 'classes'), _netclasses(rules.files['netclass_definitions_file']))
    except JsonTextError as exc:
        raise StandardsError(str(exc)) from exc
    return updated


def _change(root: Path, path: Path, before: bytes | None, text: str, bom: bool) -> Change | None:
    after = _encode(text, bom)
    if after == before:
        return None
    return Change(_relative(root, path), before, after)


def _plan(project_file: Path, board_file: Path, rules: Preset | None, stackup: Preset | None,
          catalog: Catalog | None, *, allow_rule_adoption: bool = False) -> StandardsPlan:
    if rules is None and stackup is None:
        raise StandardsError('Select a rules or stackup preset first.')
    for path in (project_file, board_file):
        if path.is_symlink():
            raise StandardsError(f'{path.name} uses a symbolic link. Edit the real project instead.')
    root = project_file.parent.resolve(strict=True)
    project_path = project_file.resolve(strict=True)
    board_path = board_file.resolve(strict=True)
    dru_path = board_path.with_suffix('.kicad_dru')
    if dru_path.is_symlink() or (root / PROVENANCE).is_symlink():
        raise StandardsError('Symbolic links in the project files are unsupported.')
    provenance_path = root / PROVENANCE
    project_bytes = _read_bytes(project_path, 8 * 1024 * 1024)
    board_bytes = _read_bytes(board_path, FILE_LIMIT)
    if project_bytes is None or board_bytes is None:
        raise StandardsError('Save the KiCad project and board before applying standards.')
    project_text, project_bom = _decode(project_bytes, project_path.name)
    board_text, board_bom = _decode(board_bytes, board_path.name)
    dru_bytes = _read_bytes(dru_path, 2 * 1024 * 1024)
    dru_text, dru_bom = _decode(dru_bytes, dru_path.name) if dru_bytes is not None else ('', False)
    provenance_bytes = _read_bytes(provenance_path, 1024 * 1024)
    provenance_text, provenance_bom = _decode(provenance_bytes, PROVENANCE) if provenance_bytes is not None else (None, False)
    if not re.match(r'\s*\(kicad_pcb(?:\s|\))', board_text):
        raise StandardsError('Expected a KiCad board file.')
    major = _board_major(board_text)
    for preset in (rules, stackup):
        if preset is not None and major not in preset.details.get('compatible_kicad_majors', [9]):
            raise StandardsError(f'The preset {preset.name!r} has not been qualified for KiCad {major}.')
    commit, dirty = _source_identity(catalog.root if catalog else None)
    catalog_name = catalog.name if catalog else ''
    summary, changes, missing_dependencies = [], [], []
    adopts_existing_rules = False
    if rules is not None:
        summary += ['', 'Rules: ' + rules.name,
                    'Custom rules update one marked company-rules block. Rules outside that block are kept.',
                    'KiCad gives later rules priority, so project rules below the block override the company rules.']
        project_json = json.loads(project_text)
        classes = project_json.get('net_settings', {}).get('classes', [])
        if not isinstance(classes, list) or any(not isinstance(item, dict) or not isinstance(item.get('name'), str) for item in classes):
            raise StandardsError('Project net class definitions are invalid.')
        areas, nets = _areas_and_nets(board_text)
        class_names = {item['name'] for item in classes}
        for key, names, label in [('required_netclasses', class_names, 'Netclasses'),
                                  ('referenced_rule_areas', areas, 'Named areas'),
                                  ('referenced_net_names', nets, 'Named nets')]:
            missing = set(rules.details.get(key, [])) - names
            summary.append(f'{label} missing from the saved project: ' + (', '.join(sorted(missing)) or 'none'))
            missing_dependencies.extend(f'{label}: {name}' for name in sorted(missing))
        if 'netclass_definitions_file' in rules.files:
            summary.append('Net class definitions in the preset are updated by name. Assignments and other net classes are kept.')
        merged_project = _merge_project(project_text, rules)
        project_change = _change(root, project_path, project_bytes, merged_project, project_bom)
        if project_change:
            changes.append(project_change)
        merged_dru = merge_custom_rules(dru_text, rules, commit=commit, dirty=dirty,
                                        allow_rule_adoption=allow_rule_adoption)
        adopts_existing_rules = (COMPANY_BEGIN not in dru_text and
                                bool(set(_rule_names(dru_text)) & set(_rule_names(rules.files['custom_rules_file']))))
        if adopts_existing_rules:
            old = [node for name, node in forms(dru_text) if name == 'rule']
            incoming = [node for name, node in forms(rules.files['custom_rules_file']) if name == 'rule']
            changed_rules = [atoms(node)[1] for current, node in zip(old, incoming) if canonical(current) != canonical(node)]
            summary.append('Applying will adopt the ' + ('existing' if changed_rules else 'identical existing')
                           + ' rule prefix into the managed company block. Rule order and later project exceptions are retained.')
            if changed_rules:
                summary.append('Existing shared rules that will change (review the exact diff): ' + ', '.join(changed_rules))
        dru_change = _change(root, dru_path, dru_bytes, merged_dru, dru_bom)
        if dru_change:
            changes.append(dru_change)
    if stackup is not None:
        merged_board, old_names, new_names, thickness = merge_board(board_text, stackup)
        removed = [name for name in old_names if name not in new_names]
        if old_names == new_names:
            note = 'same count'
        elif not removed:
            note = 'copper layers will be added; other layers stay'
        elif len(new_names) < len(old_names) and not (set(new_names) - set(old_names)):
            note = 'unused copper layers will be removed'
        else:
            note = 'copper layer list will change; other layers stay'
        from .stackup import board_stackup_from_text, physical_summary
        effective = board_stackup_from_text(merged_board).physical
        summary += ['', 'Stackup: ' + stackup.name,
                    f'Copper layers: {len(old_names)} → {len(new_names)} ({note})',
                    f'Total thickness: {thickness:g} → {effective.total_mm:g} mm',
                    physical_summary(effective),
                    'Copper, dielectric, and copper-finish entries follow the preset. Other physical layers, such as mask and silk, stay unless the preset includes them.']
        board_change = _change(root, board_path, board_bytes, merged_board, board_bom)
        if board_change:
            changes.append(board_change)
    if rules is not None and rules.status == 'draft':
        summary.append('A selected preset is still a draft. Review it before building a board from it.')
    provenance = _merge_provenance(provenance_text, rules, stackup, catalog_name, commit, dirty)
    provenance_change = _change(root, provenance_path, provenance_bytes, provenance, provenance_bom)
    design_changes = list(changes)
    if provenance_change:
        changes.append(provenance_change)
    if not design_changes:
        summary.append('The compared settings already match the selected presets.')
        if provenance_change:
            summary.append(f'Applying will record the preset in {PROVENANCE}.')
    lock_message = editor_lock_message(root)
    if lock_message:
        summary.append(lock_message)
    return StandardsPlan(root, tuple(changes), '\n'.join(summary).strip(), tuple(missing_dependencies), adopts_existing_rules)


def plan_standards(project_file: Path, board_file: Path, rules: Preset | None, stackup: Preset | None,
                   catalog: Catalog | None = None, *, allow_rule_adoption: bool = False) -> StandardsPlan:
    try:
        return _plan(project_file, board_file, rules, stackup, catalog, allow_rule_adoption=allow_rule_adoption)
    except StandardsError:
        raise
    except (ExpressionError, JsonTextError, OSError, ValueError, IndexError, KeyError, TypeError) as exc:
        raise StandardsError(f'Cannot prepare standards changes: {exc}') from exc


def apply_standards(plan: StandardsPlan, storage: Path | None = None) -> Path | None:
    message = editor_lock_message(plan.root)
    if message:
        raise EditorsOpenError(message)
    try:
        return commit_changes(plan.root, plan.changes, storage, limit=FILE_LIMIT)
    except SetupError as exc:
        raise StandardsError(str(exc)) from exc
