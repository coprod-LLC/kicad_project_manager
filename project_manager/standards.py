"""Local standards catalog, saved-file comparison, and application."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path, PurePosixPath
import re

from .sexpr import atoms, forms


class StandardsError(ValueError):
    pass


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    kind: str
    status: str
    details: dict
    files: dict[str, str]


@dataclass(frozen=True)
class Catalog:
    root: Path
    name: str
    rules: tuple[Preset, ...]
    stackups: tuple[Preset, ...]


@dataclass(frozen=True)
class Comparison:
    summary: str
    diff: str


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StandardsError(f'Duplicate JSON key: {key}.')
        result[key] = value
    return result


def _json(text: str) -> dict:
    try:
        value = json.loads(text, object_pairs_hook=_object,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except ValueError as exc:
        raise StandardsError(f'Invalid JSON: {exc}') from exc
    if not isinstance(value, dict):
        raise StandardsError('Expected a JSON object.')
    return value


def _text(path: Path, limit: int = 2 * 1024 * 1024) -> str:
    try:
        if not path.is_file() or path.stat().st_size > limit:
            raise StandardsError(f'{path.name} must be a regular file smaller than {limit // 1024} KB.')
        # Bound the read too, in case the file grows between stat and open.
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise StandardsError(f'{path.name} is too large.')
        return data.decode('utf-8-sig')
    except (OSError, UnicodeError) as exc:
        raise StandardsError(f'Cannot read {path.name}: {exc}') from exc


def _file(root: Path, relative: str) -> str:
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise StandardsError('Preset file paths must be relative paths inside the standards folder.')
    path = PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or ':' in relative:
        raise StandardsError('Preset file paths must stay inside the standards folder.')
    candidate = root
    for part in path.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise StandardsError(f'Symbolic links inside the catalog are unsupported: {relative}.')
    return _text(candidate)


def _string(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise StandardsError(f'{label} must be nonempty single-line text.')
    return value


def _strings(value, label: str) -> list[str]:
    if not isinstance(value, list):
        raise StandardsError(f'{label} must be a list of names.')
    return [_string(item, label) for item in value]


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise StandardsError(f'{label} must be a positive number.')
    return value


def _children(text: str) -> dict[str, list[str]]:
    result = {}
    for name, node in forms(text, children=True):
        result.setdefault(name, []).append(node)
    return result


def _one(nodes: dict, name: str) -> str:
    values = nodes.get(name, [])
    if len(values) != 1:
        raise StandardsError(f'Expected exactly one {name} expression.')
    return values[0]


def _root(text: str, expected: str) -> dict:
    nodes = list(forms(text))
    if len(nodes) != 1 or nodes[0][0] != expected:
        raise StandardsError(f'Expected one {expected} expression.')
    return _children(nodes[0][1])


def _constraints(text: str) -> dict:
    data = _json(text)
    try:
        board = data['board']
        settings = board['design_settings']
        rules = settings['rules']
        if set(data) != {'board'} or set(board) != {'design_settings'} or set(settings) != {'rules'}:
            raise StandardsError('Constraints must contain only board.design_settings.rules.')
        if not isinstance(rules, dict) or not rules:
            raise StandardsError('Constraints must be a nonempty object.')
        for key, value in rules.items():
            if not isinstance(key, str) or not isinstance(value, (int, float, bool)) or not math.isfinite(value):
                raise StandardsError('Constraint values must be finite numbers or booleans.')
        return rules
    except (KeyError, TypeError) as exc:
        raise StandardsError('Constraints must contain board.design_settings.rules.') from exc


def _netclasses(text: str) -> list[dict]:
    data = _json(text)
    try:
        settings = data['net_settings']
        classes = settings['classes']
        if set(data) != {'net_settings'} or set(settings) != {'classes'} or not isinstance(classes, list):
            raise StandardsError('Netclass definitions must contain only net_settings.classes.')
        names = [_string(c.get('name'), 'Netclass name') for c in classes if isinstance(c, dict)]
        if len(names) != len(classes) or len(names) != len(set(names)):
            raise StandardsError('Netclass definitions require unique names.')
        return classes
    except (KeyError, TypeError) as exc:
        raise StandardsError('Invalid netclass definitions.') from exc


def _stackup_data(native: str) -> tuple[dict, str]:
    """Derive neutral KiCad 9 copper declarations and summaries from one definition."""
    from .stackup import parse_stackup
    physical = parse_stackup(native)
    copper = []
    for layer in physical.copper:
        if layer.thickness_mm is None or layer.thickness_mm <= 0:
            raise StandardsError('Each copper layer requires one positive thickness.')
        copper.append({'layer': layer.name, 'thickness_mm': layer.thickness_mm})
    expected = ['F.Cu', *[f'In{i}.Cu' for i in range(1, len(copper) - 1)], 'B.Cu']
    if not 2 <= len(copper) <= 32 or len(copper) % 2 or [c['layer'] for c in copper] != expected:
        raise StandardsError('Copper layers must be in physical order, F.Cu through B.Cu, with an even count.')
    declarations = ['(layers']
    for index, name in enumerate(expected):
        number = 0 if index == 0 else 2 if index == len(expected) - 1 else 2 * index + 2
        declarations.append(f'  ({number} "{name}" signal)')
    declarations.append(')')
    return {'copper_layer_count': len(copper), 'board_thickness_mm': _number(physical.total_mm, 'Stackup thickness'),
            'copper': copper}, '\n'.join(declarations) + '\n'


def _stackup_documents(root: Path) -> list[tuple[dict, str, str]]:
    directory = root / 'stackups'
    if directory.is_symlink():
        raise StandardsError('Symbolic links inside the catalog are unsupported: stackups.')
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise StandardsError('stackups must be a directory.')
    documents = []
    def walk_error(error):
        raise error
    for current, dirs, names in os.walk(directory, onerror=walk_error, followlinks=False):
        dirs.sort()
        for name in (*dirs, *names):
            path = Path(current) / name
            if path.is_symlink():
                raise StandardsError(f'Symbolic links inside the catalog are unsupported: {path.relative_to(root)}.')
        for name in sorted(names):
            if not name.endswith('.json'):
                continue
            relative = (Path(current) / name).relative_to(root).as_posix()
            text = _file(root, relative)
            try:
                documents.append((_json(text), text, relative))
            except StandardsError as exc:
                raise StandardsError(f'{relative}: {exc}') from exc
            if len(documents) > 100:
                raise StandardsError('stackups must contain at most 100 presets.')
    return documents


def _embedded_stackup(entry: dict, source: tuple[str, str]) -> tuple[dict, dict]:
    text, relative = source
    if 'status' in entry:
        raise StandardsError('Stackup presets have no review status; remove status from this document.')
    if type(entry.get('schema_version')) is not int or entry['schema_version'] != 1:
        raise StandardsError('Stackup documents must use schema_version 1.')
    if type(entry.get('kicad_major')) is not int or entry['kicad_major'] != 9:
        raise StandardsError('Stackup documents must target KiCad 9.')
    derived_fields = {'copper_layer_count', 'board_thickness_mm', 'copper',
                      'stackup_file', 'copper_layers_file', 'preset_file'}
    if derived_fields.intersection(entry):
        raise StandardsError('Stackup documents must derive copper layers and thickness from stackup; remove summary and file-path fields.')
    lines = entry.get('stackup')
    if (not isinstance(lines, list) or not lines
            or any(not isinstance(line, str) or '\n' in line or '\r' in line for line in lines)):
        raise StandardsError('stackup must be a nonempty array of native KiCad text lines.')
    if 'source_evidence' in entry and not isinstance(entry['source_evidence'], dict):
        raise StandardsError('source_evidence must be an object.')
    native = '\n'.join(lines) + '\n'
    derived, copper = _stackup_data(native)
    details = {key: value for key, value in entry.items() if key != 'stackup'}
    details.update(derived, preset_file=relative)
    return details, {'stackup_file': native, 'copper_layers_file': copper, 'preset_file': text}


def load_catalog(folder: Path) -> Catalog:
    try:
        root = folder.expanduser().resolve(strict=True)
        data = _json(_file(root, 'catalog.json'))
        version = data.get('schema_version')
        if type(version) is not int or version != 2:
            raise StandardsError('This plugin requires catalog schema_version 2 with self-contained stackup documents.')
        if type(data.get('kicad_major')) is not int or data['kicad_major'] != 9:
            raise StandardsError('Choose a catalog for KiCad 9.')
        name = _string(data.get('name'), 'Catalog name')
        if 'stackups' in data:
            raise StandardsError('Catalog schema_version 2 discovers stackups/*.json recursively; remove the stackups list.')
        documents = _stackup_documents(root)
        groups = {}
        for kind in ('rules', 'stackups'):
            entries = [entry for entry, _text, _path in documents] if kind == 'stackups' else data.get(kind)
            if not isinstance(entries, list) or len(entries) > 100:
                raise StandardsError(f'{kind} must be a list of at most 100 presets.')
            presets, seen = [], set()
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise StandardsError('Each preset must be an object.')
                pid = _string(entry.get('id'), 'Preset ID')
                if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', pid) or pid in seen:
                    raise StandardsError('Preset IDs must be unique lowercase names within each category.')
                seen.add(pid)
                title = _string(entry.get('name'), 'Preset name')
                status = entry.get('status', 'draft') if kind == 'rules' else ''
                if kind == 'rules' and status not in ('draft', 'reviewed', 'deprecated'):
                    raise StandardsError('Preset status must be draft, reviewed, or deprecated.')
                if kind == 'stackups':
                    try:
                        details, files = _embedded_stackup(entry, documents[index][1:])
                    except StandardsError as exc:
                        raise StandardsError(f'{documents[index][2]}: {exc}') from exc
                else:
                    keys = ('custom_rules_file', 'constraints_file')
                    files = {key: _file(root, entry.get(key)) for key in keys}
                    details = dict(entry)
                from .stackup import validate_metadata, stackup_label
                validate_metadata(details)
                if kind == 'rules':
                    nodes = list(forms(files['custom_rules_file']))
                    if not nodes or nodes[0][0] != 'version' or atoms(nodes[0][1]) != ['version', '1']:
                        raise StandardsError('Custom rules must start with (version 1).')
                    if any(key != 'rule' for key, _ in nodes[1:]):
                        raise StandardsError('Unsupported expression in custom rules.')
                    details['rule_count'] = len(nodes) - 1
                    _constraints(files['constraints_file'])
                    patterns = {'required_netclasses': r"hasNetclass\('([^']+)'\)",
                                'referenced_rule_areas': r"intersectsArea\('([^']+)'\)",
                                'referenced_net_names': r"NetName\s*(?:==|!=)\s*'([^']+)'"}
                    for key, pattern in patterns.items():
                        declared = _strings(entry.get(key, []), key)
                        details[key] = sorted(set(declared) | set(re.findall(pattern, files['custom_rules_file'])))
                    if 'netclass_definitions_file' in entry:
                        files['netclass_definitions_file'] = _file(root, entry['netclass_definitions_file'])
                        _netclasses(files['netclass_definitions_file'])
                else:
                    title = stackup_label(details)
                presets.append(Preset(pid, title, kind, status, details, files))
            groups[kind] = tuple(presets)
        if not groups['rules'] and not groups['stackups']:
            raise StandardsError('The catalog has no presets.')
        return Catalog(root, name, groups['rules'], groups['stackups'])
    except StandardsError:
        raise
    except (OSError, ValueError, IndexError, KeyError, TypeError) as exc:
        raise StandardsError(f'Cannot load standards catalog: {exc}') from exc


def preset_details(preset: Preset | None) -> str:
    if preset is None:
        return 'Keep the current project settings for this category.'
    data = preset.details
    lines = [preset.name + (f' ({preset.status})' if preset.status else '')]
    if preset.kind == 'stackups':
        from .stackup import parse_stackup, physical_summary, recommendation_text
        lines += [physical_summary(parse_stackup(preset.files['stackup_file'])), recommendation_text(data)]
        if data.get('used_in'):
            lines.append('Previously used in: ' + ', '.join(data['used_in']))
        if data.get('finished_thickness_mm'):
            tolerance = data.get('thickness_tolerance_percent')
            lines.append(f'Manufacturer finished thickness: {data["finished_thickness_mm"]:g} mm'
                         + (f' ±{tolerance:g}%' if tolerance else ''))
        for target in data.get('impedance_targets', []):
            lines.append(f'Routing guidance: {target["impedance_ohms"]:g} Ω {target["mode"].replace("_", " ")} · '
                         f'{target.get("geometry", "geometry unspecified")} · width {target["track_width_mm"]:g} mm'
                         + (f' / gap {target["pair_gap_mm"]:g} mm' if 'pair_gap_mm' in target else '')
                         + f' · layer {target.get("layer", "unconfirmed")} / reference {target.get("reference_layer", "unconfirmed")}'
                         + (' · ' + target['notes'] if target.get('notes') else ''))
    else:
        lines.append(f'{data["rule_count"]} custom rules')
        for key, label in [('required_netclasses', 'Netclasses'), ('referenced_rule_areas', 'Named areas'),
                           ('referenced_net_names', 'Named nets')]:
            lines.append(f'{label}: ' + (', '.join(data[key]) or 'none declared or detected'))
    for key, label in [('description', 'Purpose'), ('notes', 'Notes'), ('source_url', 'Source')]:
        if data.get(key):
            lines.append(f'{label}: {data[key]}')
    if data.get('assumptions'):
        lines.append('Assumptions: ' + '; '.join(data['assumptions']))
    if preset.status == 'draft':
        lines.append('Draft sample — review before using it on a board.')
    return '\n'.join(lines)


def compare(project_file: Path, board_file: Path, rules: Preset | None, stackup: Preset | None,
            catalog: Catalog | None = None, *, allow_rule_adoption: bool = False) -> Comparison:
    """Describe the saved-file edits an apply would make. This does not write."""
    from .standards_apply import plan_standards
    plan = plan_standards(project_file, board_file, rules, stackup, catalog, allow_rule_adoption=allow_rule_adoption)
    summary = ['Comparison uses files saved on disk. Unsaved editor changes are not included.',
               'No project files were changed.', plan.summary]
    return Comparison('\n'.join(summary), plan.diff() or 'No differences in the compared settings.')
