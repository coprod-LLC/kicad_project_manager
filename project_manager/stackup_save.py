"""Capture saved KiCad stackups and create one catalog document without overwriting."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import uuid

from .sexpr import atoms
from .standards import (Catalog, StandardsError, _embedded_stackup, _one, _root,
                        _stackup_data, _string, load_catalog)
from .standards_apply import FILE_LIMIT, _copper_names, _decode, _read_bytes
from .stackup import PhysicalStackup, _physical_key, board_stackup_from_text, parse_stackup, validate_metadata


@dataclass(frozen=True)
class CapturedStackup:
    board: Path
    board_hash: str
    physical: PhysicalStackup


@dataclass(frozen=True)
class SimilarPreset:
    preset_id: str
    name: str
    reason: str
    differences: tuple[str, ...]
    blocking: bool = False


@dataclass(frozen=True)
class SavePresetPlan:
    capture: CapturedStackup
    root: Path
    relative: str
    preset_id: str
    text: str
    catalog_signature: tuple
    matches: tuple[SimilarPreset, ...]


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '_', ascii_value).strip('_')[:48] or 'preset'


def capture_saved_stackup(board: Path) -> CapturedStackup:
    if board.is_symlink():
        raise StandardsError('Use the saved board file itself, rather than a symbolic link.')
    data = _read_bytes(board, FILE_LIMIT)
    if data is None:
        raise StandardsError('Save the board in KiCad first.')
    text, _bom = _decode(data, board.name)
    top = _root(text, 'kicad_pcb')
    if atoms(_one(top, 'version')) != ['version', '20241229']:
        raise StandardsError('Saving stackup presets requires the KiCad 9 board format (20241229).')
    physical = board_stackup_from_text(text).physical
    if physical is None:
        raise StandardsError('Configure the physical stackup in Board Setup and save the board first.')
    derived, _layers = _stackup_data(physical.native)
    if _copper_names(_one(top, 'layers')) != [row['layer'] for row in derived['copper']]:
        raise StandardsError('The saved physical stackup disagrees with the enabled copper layers. Fix it in KiCad and save again.')
    return CapturedStackup(board.resolve(strict=True), _hash(data), physical)


def _identity(value: str | None) -> str:
    return ''.join(c for c in (value or '').casefold() if c.isalnum())


def _construction(stack):
    return [layer for layer in stack.layers if layer.kind in ('copper', 'core', 'prepreg')]


def _differences(existing, current) -> tuple[str, ...]:
    left, right = _construction(existing), _construction(current)
    if len(left) != len(right):
        return (f'Copper/dielectric ply count: {len(left)} → {len(right)}',)
    differences = []
    for a, b in zip(left, right):
        for key, label, unit in [('kind', 'type', ''), ('material', 'material', ''),
                                 ('thickness_mm', 'thickness', ' mm'), ('epsilon_r', 'εr', ''),
                                 ('loss_tangent', 'loss tangent', '')]:
            old, new = getattr(a, key), getattr(b, key)
            if old == new or (isinstance(old, (float, int)) and isinstance(new, (float, int))
                              and math.isclose(old, new, rel_tol=0, abs_tol=1e-9)):
                continue
            def display(value):
                return f'{value:g}' if isinstance(value, (int, float)) else value or 'unspecified'
            differences.append(f'{b.label} {label}: {display(old)} → {display(new)}{unit}')
    return tuple(differences)


def _near(existing, current) -> bool:
    left, right = _construction(existing), _construction(current)
    if len(existing.copper) != len(current.copper) or len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if a.kind != b.kind or a.thickness_mm is None or b.thickness_mm is None:
            return False
        if abs(a.thickness_mm - b.thickness_mm) > max(0.005, 0.1 * max(a.thickness_mm, b.thickness_mm)):
            return False
    return True


def similar_presets(physical: PhysicalStackup, metadata: dict, catalog: Catalog) -> tuple[SimilarPreset, ...]:
    matches = []
    manufacturer = _identity(metadata.get('manufacturer', ''))
    code = _identity(metadata.get('manufacturer_stackup_id', ''))
    construction = _physical_key(physical, construction=True)
    for preset in catalog.stackups:
        other = parse_stackup(preset.files['stackup_file'])
        same_manufacturer = bool(manufacturer and manufacturer == _identity(preset.details.get('manufacturer', '')))
        same_code = bool(same_manufacturer and code and code == _identity(preset.details.get('manufacturer_stackup_id', '')))
        exact = construction == _physical_key(other, construction=True)
        if same_code or exact or _near(other, physical):
            blocking = same_code or (exact and same_manufacturer)
            reason = ('Manufacturer and construction code already exist' if same_code else
                      'Identical copper/dielectric construction' if exact else 'Similar copper/dielectric dimensions')
            differences = _differences(other, physical)
            matches.append(SimilarPreset(preset.id, preset.name, reason, differences, blocking))
    return tuple(sorted(matches, key=lambda match: (not match.blocking, match.name.casefold(), match.preset_id)))


def _signature(catalog: Catalog) -> tuple:
    return (catalog.name, tuple(sorted((preset.id, preset.details['preset_file'],
                                      _hash(preset.files['preset_file'].encode('utf-8')))
                                     for preset in catalog.stackups)))


def prepare_preset(capture: CapturedStackup, catalog: Catalog, metadata: dict,
                   *, preset_id: str | None = None) -> SavePresetPlan:
    metadata = dict(metadata)
    allowed = {'name', 'manufacturer', 'manufacturer_stackup_id', 'nominal_thickness_mm',
               'finished_thickness_mm', 'manufacturer_compression_thickness_mm',
               'thickness_tolerance_percent', 'outer_copper_oz', 'inner_copper_oz',
               'manufacturer_recommended', 'recommendation_checked', 'recommendation_evidence',
               'manufacturer_geometry_checked', 'preferred', 'source_url', 'notes', 'assumptions', 'impedance_targets'}
    if set(metadata) - allowed:
        raise StandardsError('Unsupported preset metadata: ' + ', '.join(sorted(set(metadata) - allowed)))
    manufacturer = _string(metadata.get('manufacturer'), 'Manufacturer').strip()
    name = _string(metadata.get('name'), 'Preset name').strip()
    metadata.update(manufacturer=manufacturer, name=name)
    validate_metadata(metadata)
    copper = {layer.name for layer in capture.physical.copper}
    for target in metadata.get('impedance_targets', []):
        if target.get('layer') not in copper or target.get('reference_layer') not in copper:
            raise StandardsError('Each impedance guide needs signal and reference layers from this saved stackup.')
        if target['layer'] == target['reference_layer']:
            raise StandardsError('Signal and reference layers must be different.')
        _string(target.get('geometry'), 'Impedance geometry')
    if len(catalog.stackups) >= 100:
        raise StandardsError('This catalog already contains the maximum of 100 stackups.')
    if preset_id is None:
        prefix = _slug(manufacturer + '_' + (metadata.get('manufacturer_stackup_id') or name))
        preset_id = prefix + '_' + uuid.uuid4().hex[:8]
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', preset_id):
        raise StandardsError('Invalid preset ID.')
    if any(p.id == preset_id for p in catalog.stackups):
        raise StandardsError('This preset ID already exists in the catalog.')
    evidence = {'captured_on': date.today().isoformat()}
    if metadata.get('source_url'):
        evidence['source_page'] = metadata['source_url']
    document = {'schema_version': 1, 'kicad_major': 9, 'id': preset_id,
                'manufacturer_recommended': None, 'preferred': False, **metadata,
                'source_evidence': evidence, 'stackup': capture.physical.native.splitlines()}
    text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if len(text.encode('utf-8')) > 2 * 1024 * 1024:
        raise StandardsError('The stackup preset exceeds the 2 MiB document limit.')
    relative = f'stackups/{_slug(manufacturer)}/{preset_id}.json'
    _embedded_stackup(document, (text, relative))
    return SavePresetPlan(capture, catalog.root, relative, preset_id, text, _signature(catalog),
                          similar_presets(capture.physical, metadata, catalog))


def save_preset(plan: SavePresetPlan, *, allow_similar: bool = False) -> Catalog:
    """Recheck the source and catalog, then atomically publish one new file."""
    if not re.fullmatch(r'stackups/[a-z0-9_]+/[a-z0-9_-]+\.json', plan.relative):
        raise StandardsError('The preset destination must be inside stackups/<manufacturer>/.')
    if any(match.blocking for match in plan.matches):
        raise StandardsError('This manufacturer/code or identical construction already exists. Use the existing preset.')
    if plan.matches and not allow_similar:
        raise StandardsError('Review the similar presets before saving a distinct construction.')
    capture = plan.capture
    data = _read_bytes(capture.board, FILE_LIMIT)
    if data is None or _hash(data) != capture.board_hash:
        raise StandardsError('The saved board changed while this form was open. Close it and capture the saved stackup again.')
    current = load_catalog(plan.root)
    if _signature(current) != plan.catalog_signature:
        raise StandardsError('The catalog changed while this form was open. Close it and check the catalog again.')
    target = plan.root / plan.relative
    parent = plan.root
    for part in Path(plan.relative).parts[:-1]:
        parent /= part
        if parent.is_symlink():
            raise StandardsError('Symbolic links inside the catalog are unsupported.')
        parent.mkdir(exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise StandardsError('The preset destination must be a real directory inside the catalog.')
    fd, temporary = tempfile.mkstemp(prefix='.kpm-preset-', suffix='.tmp', dir=target.parent)
    created = False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(plan.text)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link publishes the complete file atomically and refuses an existing destination.
        os.link(temporary, target)
        created = True
        return load_catalog(plan.root)
    except Exception:
        if created:
            try:
                if target.read_bytes() == plan.text.encode('utf-8'):
                    target.unlink()
            except OSError:
                pass
        raise
    finally:
        Path(temporary).unlink(missing_ok=True)
