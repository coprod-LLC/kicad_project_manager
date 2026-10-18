"""Lossless physical stackup reading and manufacturer-oriented catalog entries."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import hashlib
import math
from pathlib import Path
from urllib.parse import urlparse

from .sexpr import atoms, spans, tokens
from .standards import StandardsError, Preset, _children, _one, _root, _string, _strings, _number


@dataclass(frozen=True)
class PhysicalLayer:
    name: str
    kind: str
    sublayer: int = 0
    sublayer_count: int = 1
    material: str = ''
    thickness_mm: float | None = None
    color: str = ''
    epsilon_r: float | None = None
    loss_tangent: float | None = None
    locked: bool = False

    @property
    def key(self):
        return self.name, self.sublayer

    @property
    def label(self):
        name = self.name.replace('dielectric ', 'D')
        return f'{name}.{self.sublayer + 1}' if self.sublayer_count > 1 else name


@dataclass(frozen=True)
class PhysicalStackup:
    native: str
    layers: tuple[PhysicalLayer, ...]
    finish: str
    impedance_controlled: bool | None

    @property
    def total_mm(self):
        return sum(layer.thickness_mm or 0 for layer in self.layers)

    @property
    def copper(self):
        return tuple(layer for layer in self.layers if layer.kind == 'copper')


@dataclass(frozen=True)
class BoardStackup:
    physical: PhysicalStackup | None
    board_thickness_mm: float | None


@dataclass(frozen=True)
class StackupMatch:
    preset: Preset
    exact: bool


def stackup_identity(preset: Preset) -> str:
    manufacturer = preset.details.get('manufacturer')
    code = preset.details.get('manufacturer_stackup_id')
    return f'{manufacturer} · {code}' if manufacturer and code else preset.name


def _physical_key(stack: PhysicalStackup, *, construction: bool = False):
    layers = []
    for layer in stack.layers:
        if construction and layer.kind not in ('copper', 'core', 'prepreg'):
            continue
        # Normalize floating-point representation noise; retain each ordered ply.
        numbers = {key: None if (value := getattr(layer, key)) is None else round(value, 9)
                   for key in ('thickness_mm', 'epsilon_r', 'loss_tangent')}
        layers.append(replace(layer, **numbers,
                              color='' if construction else layer.color,
                              locked=False if construction else layer.locked))
    return tuple(layers) if construction else (tuple(layers), stack.finish, stack.impedance_controlled)


def matching_stackups(current: PhysicalStackup | None, presets: tuple[Preset, ...]) -> tuple[StackupMatch, ...]:
    """Identify saved construction data, with exact physical matches taking priority.

    Surface layers, colours, finish and editor settings cannot change a copper /
    dielectric construction's identity, but differences must still be disclosed.
    Multiple matches are returned so identical definitions never imply a supplier.
    """
    if current is None or not current.copper:
        return ()
    construction, physical = _physical_key(current, construction=True), _physical_key(current)
    matches = []
    for preset in presets:
        expected = parse_stackup(preset.files['stackup_file'])
        if construction == _physical_key(expected, construction=True):
            matches.append(StackupMatch(preset, physical == _physical_key(expected)))
    exact = [match for match in matches if match.exact]
    return tuple(exact or matches)


def _field(fields, name, default=''):
    if name not in fields:
        return default
    values = atoms(_one(fields, name))
    if len(values) != 2:
        raise StandardsError(f'Invalid stackup {name} field.')
    return values[1]


def _optional_number(fields, name):
    value = _field(fields, name, None)
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise StandardsError(f'Stackup {name} must be finite and nonnegative.')
    return number


def parse_stackup(text: str) -> PhysicalStackup:
    """Keep every dielectric ply, including bare ``addsublayer`` separators."""
    try:
        physical = _root(text, 'stackup')
        layers, seen = [], set()
        for node in physical.get('layer', []):
            values = atoms(node)
            name = values[1]
            if name in seen:
                raise StandardsError(f'Duplicate physical layer: {name}.')
            seen.add(name)
            depth, boundaries = 0, []
            for token, left, right in tokens(node):
                if token == '(':
                    depth += 1
                elif token == ')':
                    depth -= 1
                elif depth == 1 and token == 'addsublayer':
                    boundaries.append((left, right))
            # The first chunk includes the layer name; subsequent chunks contain fields only.
            first_end = boundaries[0][0] if boundaries else len(node) - 1
            chunks = [node[:first_end] + ')']
            chunks += ['(ply ' + node[right:(boundaries[i + 1][0] if i + 1 < len(boundaries) else len(node) - 1)] + ')'
                       for i, (_left, right) in enumerate(boundaries)]
            kind = _field(_children(chunks[0]), 'type')
            if not kind:
                raise StandardsError(f'Physical layer {name} has no type.')
            if boundaries and kind not in ('core', 'prepreg'):
                raise StandardsError('Only dielectric layers can have sublayers.')
            for index, chunk in enumerate(chunks):
                fields = _children(chunk)
                thickness, locked = None, False
                if 'thickness' in fields:
                    entry = atoms(_one(fields, 'thickness'))
                    if len(entry) not in (2, 3) or (len(entry) == 3 and entry[2] != 'locked'):
                        raise StandardsError('Invalid stackup thickness field.')
                    thickness, locked = float(entry[1]), len(entry) == 3
                    if not math.isfinite(thickness) or thickness < 0:
                        raise StandardsError('Layer thicknesses must be finite and nonnegative.')
                layers.append(PhysicalLayer(name, kind, index, len(chunks),
                                            _field(fields, 'material'), thickness, _field(fields, 'color'),
                                            _optional_number(fields, 'epsilon_r'),
                                            _optional_number(fields, 'loss_tangent'), locked))
        flag = _field(physical, 'dielectric_constraints', None)
        if flag not in (None, 'yes', 'no'):
            raise StandardsError('Impedance controlled must be yes or no.')
        return PhysicalStackup(text, tuple(layers), _field(physical, 'copper_finish'),
                               None if flag is None else flag == 'yes')
    except StandardsError:
        raise
    except (ValueError, IndexError, TypeError) as exc:
        raise StandardsError(f'Cannot read physical stackup: {exc}') from exc


def read_board_stackup(path: Path) -> BoardStackup:
    from .standards_apply import FILE_LIMIT, _read_bytes, _decode
    data = _read_bytes(path, FILE_LIMIT)
    if data is None:
        raise StandardsError('Save the board before reading its stackup.')
    text, _bom = _decode(data, path.name)
    return board_stackup_from_text(text)


def board_stackup_from_text(text: str) -> BoardStackup:
    top = _root(text, 'kicad_pcb')
    setup = _children(_one(top, 'setup'))
    stack = parse_stackup(_one(setup, 'stackup')) if 'stackup' in setup else None
    general = _children(_one(top, 'general'))
    thickness = _optional_number(general, 'thickness')
    return BoardStackup(stack, thickness)


def validate_metadata(entry: dict) -> None:
    for key in ('manufacturer', 'manufacturer_stackup_id', 'description', 'notes', 'recommendation_evidence'):
        if entry.get(key) is not None:
            _string(entry[key], key)
    for key in ('nominal_thickness_mm', 'finished_thickness_mm', 'manufacturer_compression_thickness_mm',
                'thickness_tolerance_percent', 'outer_copper_oz', 'inner_copper_oz'):
        if entry.get(key) is not None:
            _number(entry[key], key)
    for key in ('manufacturer_recommended', 'preferred'):
        if entry.get(key) is not None and type(entry[key]) is not bool:
            raise StandardsError(f'{key} must be true, false, or null.')
    for key in ('recommendation_checked', 'manufacturer_geometry_checked'):
        if entry.get(key) is not None:
            value = _string(entry[key], key)
            if date.fromisoformat(value).isoformat() != value:
                raise StandardsError(f'{key} must use YYYY-MM-DD.')
    if entry.get('source_url') is not None:
        value = urlparse(_string(entry['source_url'], 'Source URL'))
        if value.scheme not in ('https', 'http') or not value.netloc:
            raise StandardsError('Source URL must be an HTTP or HTTPS link.')
    for key in ('used_in', 'assumptions', 'compatible_stackups'):
        if key in entry:
            _strings(entry[key], key)
    if 'impedance_targets' in entry:
        if not isinstance(entry['impedance_targets'], list):
            raise StandardsError('Impedance targets must be a list.')
        for target in entry['impedance_targets']:
            if not isinstance(target, dict) or target.get('mode') not in ('differential', 'single_ended'):
                raise StandardsError('Each impedance target needs a differential or single_ended mode.')
            for key in ('impedance_ohms', 'track_width_mm'):
                _number(target.get(key), key)
            if target['mode'] == 'differential':
                _number(target.get('pair_gap_mm'), 'Differential pair gap')
            for key in ('geometry', 'layer', 'reference_layer', 'notes'):
                if key in target:
                    _string(target[key], key)


def stackup_label(entry: dict) -> str:
    manufacturer = entry.get('manufacturer')
    if not manufacturer:
        return entry['name']
    parts = [manufacturer, entry.get('manufacturer_stackup_id') or 'code pending',
             f'{entry["copper_layer_count"]} layers']
    thickness = entry.get('nominal_thickness_mm')
    parts.append(f'{thickness:g} mm nominal' if thickness else f'{entry["board_thickness_mm"]:g} mm stackup')
    if entry.get('outer_copper_oz') and entry.get('inner_copper_oz'):
        parts.append(f'{entry["outer_copper_oz"]:g} oz outer / {entry["inner_copper_oz"]:g} oz inner')
    else:
        copper = entry['copper']
        parts.append(f'{copper[0]["thickness_mm"] * 1000:g} µm outer' +
                     (f' / {copper[1]["thickness_mm"] * 1000:g} µm inner' if len(copper) > 2 else ''))
    return ' · '.join(parts)


def recommendation_text(entry: dict) -> str:
    recommended = entry.get('manufacturer_recommended')
    text = 'Manufacturer recommendation: ' + ('unknown' if recommended is None else 'yes' if recommended else 'no')
    if entry.get('recommendation_checked'):
        text += ' (checked ' + entry['recommendation_checked'] + ')'
    if entry.get('preferred'):
        text += ' · Coprod preferred'
    return text


def physical_summary(stack: PhysicalStackup | None, board_thickness: float | None = None) -> str:
    if stack is None:
        return 'No physical stackup saved. Configure one in KiCad or choose a preset.'
    flag = 'unspecified' if stack.impedance_controlled is None else 'yes' if stack.impedance_controlled else 'no'
    lines = [f'{len(stack.copper)} copper layers · {stack.total_mm:g} mm including masks',
             f'Impedance controlled: {flag} · Finish: {stack.finish or "unspecified"}']
    if board_thickness is not None and not math.isclose(board_thickness, stack.total_mm, abs_tol=1e-6):
        lines.append(f'Board thickness setting: {board_thickness:g} mm (differs from layer sum)')
    return '\n'.join(lines)


def appearance_override(preset: Preset, mask: str = '', silk: str = '', finish: str = '') -> Preset:
    """Change only explicitly selected appearance fields; retain every physical ply."""
    import json
    if not (mask or silk or finish):
        return preset
    text = preset.files['stackup_file']
    operations = []
    finish_seen = False
    for name, start, end in spans(text, children=True):
        node = text[start:end]
        if name == 'copper_finish':
            finish_seen = True
            if finish:
                operations.append((start, end, '(copper_finish ' + json.dumps(finish) + ')'))
        if name != 'layer':
            continue
        layer = atoms(node)[1]
        color = mask if layer.endswith('.Mask') else silk if layer.endswith('.SilkS') else ''
        if not color:
            continue
        fields = [item for item in spans(node, children=True) if item[0] == 'color']
        replacement = '(color ' + json.dumps(color) + ')'
        if fields:
            _key, left, right = fields[0]
            operations.append((start + left, start + right, replacement))
        else:
            operations.append((end - 1, end - 1, ' ' + replacement))
    if finish and not finish_seen:
        operations.append((len(text.rstrip()) - 1, len(text.rstrip()) - 1,
                           ' (copper_finish ' + json.dumps(finish) + ')'))
    for start, end, replacement in sorted(operations, reverse=True):
        text = text[:start] + replacement + text[end:]
    overrides = {key: value for key, value in [('mask_color', mask), ('silkscreen_color', silk), ('finish', finish)] if value}
    return replace(preset, files={**preset.files, 'stackup_file': text},
                   details={**preset.details, 'applied_overrides': overrides,
                            'source_file_hashes': {key: hashlib.sha256(value.encode()).hexdigest()
                                                   for key, value in preset.files.items()}})
