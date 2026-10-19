# Standards catalog format

The plugin requires catalog schema 2 for KiCad 9 and 10. The loader rejects
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
required. Stackup documents have no review status; a `status` field is rejected. Copper
layers must have positive thickness and be ordered F.Cu, consecutive InN.Cu,
B.Cu, with an even count from 2 through 32. Aliases and copper roles belong to
each project and are preserved on apply.

## Optional metadata

- Manufacturer: `manufacturer`, `manufacturer_stackup_id`, `source_url` and an
  embedded `source_evidence` object for captured facts, dates and references.
- Order specifications: `nominal_thickness_mm`, `outer_copper_oz`,
  `inner_copper_oz`, `finished_thickness_mm`, `thickness_tolerance_percent`,
  `manufacturer_compression_thickness_mm`. These are separate from the KiCad sum.
- Manufacturer preference: `manufacturer_recommended` (true/false/null), `recommendation_checked`,
  `recommendation_evidence`, `manufacturer_geometry_checked` and `preferred`.
  Dates use YYYY-MM-DD; internal preference and manufacturer preference are
  independent. The UI calls the boolean field **Manufacturer’s preferred option**:
  Yes / No / Unknown. No means an alternative option, not discouraged; the stored
  key remains generic and consistent with the existing constructions.
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

## Save as preset

The Stackup tab captures native text from the saved KiCad 9 or 10 board and creates a
document in exactly this format. Metadata and optional impedance guides are
validated before saving. No review-status field, local board path, project rules
or enabled-layer aliases are copied into the document.

Duplicate detection compares manufacturer/code and complete ordered copper and
dielectric properties. Mask colour, silkscreen, finish and editor locks do not
create a distinct construction. Similarity review also shows constructions with
matching ply types and thickness differences up to 10% or 0.005 mm per ply,
whichever is larger. Material, dielectric-constant and loss-tangent differences
are disclosed. Identical constructions from another manufacturer need explicit
review rather than being merged automatically.

The writer checks source-board and catalog changes again, then atomically creates
a single file without replacing an existing destination. The catalog is refreshed
and the new preset selected. Saving performs no board writes or Git operations.
## KiCad version compatibility

Catalogs and stackup documents retain `kicad_major`. The optional
`compatible_kicad_majors: [9, 10]` declares additional tested targets; omitted
means only `kicad_major`. A preset's targets must be included in its catalog's
targets. Unqualified presets are rejected before writes. Capturing a board
records its actual major; it does not automatically qualify another version.

The plugin recognizes saved PCB formats `20241229` (9) and `20260206` (10).
Unknown future formats remain blocked. Use separate standards branches while
older plugin releases are still deployed: those releases cannot load a
10-only captured preset. Existing 9-only catalogs remain supported.
