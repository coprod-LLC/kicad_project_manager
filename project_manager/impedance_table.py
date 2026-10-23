"""Lossless CSV loading and conservative imports of saved-board impedance guides."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import io
from pathlib import Path
import re

from .standards import Preset, StandardsError, load_catalog
from .stackup import matching_stackups, read_board_stackup
from .yaml_fields import SetupError

HEADERS = ('Transmission Line', 'Impedance [ohms]', 'Tolerance [ohms]', 'Layer',
           'Trace Width [mm]', 'Gap [mm]', 'Ref. Layers')
LABELS = ('Transmission line', 'Target\n(Ω)', 'Tolerance', 'Layer', 'Width\n(mm)', 'Gap\n(mm)', 'Reference\nlayers')


@dataclass(frozen=True)
class ImpedanceTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    @classmethod
    def parse(cls, value: str) -> ImpedanceTable:
        try:
            rows = [tuple(cell.strip() for cell in row) for row in csv.reader(io.StringIO(value), strict=True)
                    if any(cell.strip() for cell in row)]
        except csv.Error as exc:
            raise SetupError(f'Cannot read the impedance CSV: {exc}') from exc
        if not rows or tuple(cell.casefold() for cell in rows[0]) != tuple(cell.casefold() for cell in HEADERS):
            raise SetupError('Table view needs the seven standard impedance column headings. Use Text for other formats.')
        if any(len(row) != len(HEADERS) for row in rows[1:]):
            raise SetupError('Each impedance row needs seven comma-separated fields. Use Text to correct its format.')
        return cls(rows[0], tuple(rows[1:]))

    def render(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(self.headers)
        writer.writerows(self.rows)
        return output.getvalue()


def matching_guides(directory: Path, board: Path) -> tuple[Preset, ...]:
    catalog = load_catalog(directory)
    current = read_board_stackup(board)
    return tuple(match.preset for match in matching_stackups(current.physical, catalog.stackups)
                 if match.preset.details.get('impedance_targets'))


def _number(value: str) -> str:
    value = value.strip()
    try:
        number = Decimal(value)
        if number.is_finite():
            return str(number.normalize())
    except InvalidOperation:
        pass
    return value.casefold()


def _layer(value: str, count: int) -> str:
    value = value.strip().casefold()
    if value == 'f.cu':
        return 'l1'
    if value == 'b.cu':
        return f'l{count}'
    inner = re.fullmatch(r'in(\d+)\.cu', value)
    return f'l{int(inner[1]) + 1}' if inner else value


def _references(value: str, count: int) -> tuple[str, ...]:
    return tuple(sorted(_layer(part, count) for part in re.split(r'[;,/|+\s]+', value.strip()) if part))


def _target_key(row: tuple[str, ...], count: int):
    # Conservatively treat different geometries for the same target and signal /
    # reference layers as a conflict, rather than importing contradictory rows.
    return _number(row[1]), _layer(row[3], count), _references(row[6], count)


def add_missing_guides(table: ImpedanceTable, preset: Preset) -> tuple[ImpedanceTable, int, tuple[str, ...]]:
    """Append guidance, retaining manual rows/tolerances and reporting conflicts."""
    count = preset.details['copper_layer_count']
    rows, conflicts, added = list(table.rows), [], 0
    for target in preset.details.get('impedance_targets', []):
        if not target.get('layer') or not target.get('reference_layer'):
            conflicts.append(f'{target["impedance_ohms"]:g} Ω: add signal/reference layers to the standards profile first.')
            continue
        mode = 'Differential' if target['mode'] == 'differential' else 'Single-ended'
        row = (f'{mode} {target.get("geometry", "")}'.strip(), str(target['impedance_ohms']), '',
               _layer(target['layer'], count).upper(), str(target['track_width_mm']),
               str(target['pair_gap_mm']) if target.get('pair_gap_mm') else '',
               _layer(target['reference_layer'], count).upper())
        existing = [other for other in rows if _target_key(other, count) == _target_key(row, count)]
        if existing:
            if any((_number(other[4]), _number(other[5])) != (_number(row[4]), _number(row[5])) for other in existing):
                conflicts.append(f'{row[1]} Ω · {row[3]} / {row[6]}: existing width/gap differs; existing rows kept.')
            continue
        rows.append(row)
        added += 1
    return ImpedanceTable(table.headers, tuple(rows)), added, tuple(conflicts)
