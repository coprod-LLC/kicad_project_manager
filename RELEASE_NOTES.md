KiCad Project Manager v0.6.0 migrates standards to self-contained stackup files.

- Require catalog schema 2 and discover stackups recursively under `stackups/`.
- Keep metadata, native physical stackup, manufacturer evidence and impedance guidance in one JSON file per preset.
- Derive copper declarations and thickness summaries on load. Adding or deleting a preset changes one file, followed by Refresh.
- Record the complete preset document hash on apply, including metadata. Preserve existing preset IDs, project settings, appearance overrides and backups.
- Reject the old catalog format and duplicated stackup summaries. A committed checksum manifest is no longer required.

The Save as preset workflow is planned separately.

Validation covers 125 unit tests, native GUI flows at narrow and wide sizes,
official KiCad package schemas, and native KiCad load/save checks of the migrated
constructions. Save the project and close the PCB and schematic editors before
applying standards, then reopen the project.
