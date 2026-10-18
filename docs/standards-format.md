# Standards catalog format

Project Manager v0.6.0 requires catalog schema 2 for KiCad 9. The loader rejects
catalog schema 1. Select the folder containing `catalog.json` in Overview.

```json
{
  "schema_version": 2,
  "kicad_major": 9,
  "name": "Example standards",
  "rules": []
}
```

Rule profiles remain in `rules`: each has an ID, name, optional status, and
repository-relative `custom_rules_file` and `constraints_file` paths. Optional
netclass definitions use `netclass_definitions_file`. Assumptions and dependency
lists remain profile metadata. Missing status defaults to `draft`.

## One stackup per file

The loader recursively discovers `stackups/**/*.json` in deterministic path
order. There is no stackup list in `catalog.json`. Add, rename or delete one file,
then use Refresh. Keep the preset's ID stable when renaming its file. Deleting
the last stackup is valid when the catalog still contains rule profiles.

Each stackup document has its own format version, currently 1, independently of
the catalog version. For example, `stackups/example/ex0216.json`:

```json
{
  "schema_version": 1,
  "kicad_major": 9,
  "id": "example_ex0216",
  "name": "Example two-layer construction",
  "status": "draft",
  "manufacturer": "Example",
  "manufacturer_stackup_id": "EX0216",
  "nominal_thickness_mm": 1.6,
  "source_url": "https://example.com/stackups",
  "stackup": [
    "(stackup",
    "  (layer \"F.Cu\" (type \"copper\") (thickness 0.035))",
    "  (layer \"dielectric 1\" (type \"core\") (thickness 1.53) (material \"FR4\"))",
    "  (layer \"B.Cu\" (type \"copper\") (thickness 0.035))",
    "  (copper_finish \"ENIG\")",
    ")"
  ]
}
```

`stackup` is a nonempty array of native KiCad text lines, joined with LF and a
final newline. Include the enclosing stackup expression. Native text preserves
ordered dielectric sublayers (`addsublayer`), comments, surface layers, locks,
and additional KiCad properties without translating them into another model.
The loader derives neutral copper declarations, layer count, copper thicknesses
and physical total including masks. Do not store `copper_layer_count`, `copper`,
`board_thickness_mm`, `stackup_file`, `copper_layers_file` or `preset_file` in the
document; those duplicated summaries and file references are rejected.

IDs must match `[a-z0-9][a-z0-9_-]{0,79}` and be unique among stackups. Names are
required. Status is `draft` (the default), `reviewed` or `deprecated`. Copper
layers must have positive thickness and be ordered F.Cu, consecutive InN.Cu,
B.Cu, with an even count from 2 through 32. Aliases and copper roles belong to
each project and are preserved on apply.

## Optional metadata

- Manufacturer: `manufacturer`, `manufacturer_stackup_id`, `source_url` and an
  embedded `source_evidence` object for captured facts, dates and references.
- Order specifications: `nominal_thickness_mm`, `outer_copper_oz`,
  `inner_copper_oz`, `finished_thickness_mm`, `thickness_tolerance_percent`,
  `manufacturer_compression_thickness_mm`. These are separate from the KiCad sum.
- Review: `manufacturer_recommended` (true/false/null), `recommendation_checked`,
  `recommendation_evidence`, `manufacturer_geometry_checked` and `preferred`.
  Dates use YYYY-MM-DD; internal preference and manufacturer recommendation are
  independent.
- Context: `description`, `notes`, `used_in` and `assumptions` (the last two are
  arrays of single-line strings).
- `impedance_targets`: an array with `mode` (`single_ended` or `differential`),
  positive `impedance_ohms`, positive `track_width_mm`, and positive `pair_gap_mm`
  for differential pairs. Optional `geometry`, `layer`, `reference_layer` and
  `notes` describe the guide. Guidance does not assign nets or change widths.

## Validation and provenance

Loading validates every discovered preset; malformed files are reported rather
than skipped. Symlinks are rejected inside the catalog, documents are limited
to 2 MiB each, and each category is limited to 100 presets. The loader performs
no writes. There is no committed global checksum manifest.

Applying records the preset document's relative path and SHA-256 alongside the
expanded native stackup/copper hashes in the project's `kpm-standards.json`.
This includes metadata and source evidence in provenance. Appearance overrides
retain the original source hashes and separately record the applied hashes.
Deleting a catalog file does not modify projects that already used it.
