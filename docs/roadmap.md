# Roadmap

Steps 1 and 2 delivered the IPC shell and public release distribution in v0.1.0.
Project setup (original step 4) was moved ahead of the rules/stackup feasibility
prototype (original step 3).

## Project setup — v0.2.0 preview

- Native form for project/board names, company, authors, Git URL, and logo path.
- Suggestions from filenames and Git only for blank/template placeholder values.
- Restore a missing workflow from a locally selected template, preserving its
  branch and release policies; remember the folder on this computer.
- DRAFT/PRELIMINARY/CHECKED selection; tags still select RELEASED.
- Explicit diff preview, local backups, external-edit checks, and rollback.
- Preserve KiBot template syntax, comments, and unrelated configuration.
- KiBot updates text variables when CI runs; live KiCad project/board writes are
  left for the later API feasibility work.
- Public-template KiBot integration test; private Coprod fixtures remain local.

## Local refinements in v0.3.0

- Apply directly from Project Setup, with Preview Changes remaining optional.
  Both paths keep validation, active-project checks, backups, and conflict checks.
- Preserve existing YAML scalar quoting style where safe instead of adding
  single quotes to every edited field. Existing quoted values remain valid.
- Clarify that supported templates select RELEASED on version tags and do not
  require the manually selected status to be CHECKED first.

## Standards catalog and application prototype

Use one standalone standards folder per computer, outside individual PCB
projects. The initial source is a local folder; later it can be a private Git
checkout. No per-project submodule is required.

1. Done in v0.3.0: Settings dialog with a standards-folder picker, catalog
   schema validation, and the backup location with an Open Backups button.
2. Done in v0.3.0: Standards tab with separate Rules and Stackup dropdowns,
   dependency details, and a read-only comparison with the saved project files.
   Tests use synthetic data; company examples stay outside this repository.
3. Done in v0.3.0: apply selected presets to saved files. Custom rules update
   one marked company-rules block and leave every other rule untouched; KiCad's
   later-rule priority means project rules below that block still win. Constraints
   and named net classes are merged in `.kicad_pro`. Stackup edits copper,
   dielectrics, copper finish, and thickness, and keeps mask, silk, and paste
   unless the preset includes them. Removing a copper layer the board still uses
   is refused. `kpm-standards.json` stores the preset id, source file hashes, and
   the standards commit when the folder is a Git checkout. Apply checks editor
   lock files and file hashes, then uses the existing backup and rollback.
   The installed kicad-python client can read net classes and stackup, and it can
   set enabled layers, but it has no setters for custom rules, constraints, net
   classes, or the physical stackup, so this step edits the saved files instead
   of the open editor.
4. Add Git URL, branch/tag, local checkout, and an explicit Fetch/Update action to
   Settings. Reuse the user's Git authentication. Work offline from the local
   checkout; catalog updates must never automatically reapply project standards.

Draft extracted snapshots are preparation data, not approved fabrication limits
or an implemented importer. Review dependencies and comment/value discrepancies
before turning a project snapshot into a reusable company preset.

### KiCad 9 application feasibility

Before enabling standards application, establish a safe KiCad 9 application
path on disposable project copies. The IPC API exposes some settings for reading
without corresponding setters. An in-memory editor may overwrite external edits.

1. Build a small capability matrix for the team's actual KiCad 9 patch version.
   Check stackup, enabled layers, project constraints, netclasses, custom rules,
   saving, and reload behavior; do not assume an API client method implies server
   support.
2. Use synthetic projects first. With authorization, compare on local copies of
   representative company projects; never put those files in the public repository.
3. Prepare one deliberately simple preset with an explicit list of managed fields.
   Show a diff for `.kicad_pro`, `.kicad_dru`, and the relevant `.kicad_pcb` sections.
4. For missing setters, test a save-and-close workflow before changing files.
   Check all editors that can save shared project settings. Verify file hashes
   immediately before application; refuse intervening changes or active locks.
   Stage changes, create backups, roll back a failed multi-file operation, and
   reopen the project for verification.
5. Preserve net assignments, custom rules, unrelated settings, UUIDs, footprints,
   tracks, zones, and documentation groups. Reject removal of populated layers.
   Define company-rule versus project-exception ordering explicitly.
6. Verify the persisted settings in KiCad, a second apply produces no changes,
   cancellation leaves files untouched, and restore recovers the original bytes.
   Compare DRC results before/after without treating the preset as approval of
   the electrical design.

The file-edit mechanism above is the chosen path. Still confirm in KiCad, on a
disposable copy: closing the editors, applying, reopening, a second apply with
no further diff, and a DRC spot-check. That check does not approve the board.

## Later increments

- Extend project setup: broader template migrations and live text-variable sync
  once the safe KiCad save/reload path has been established.
- Private standards catalog: compatible rules/stackup choices, pinned source
  commits, drift detection, previews, and restoration.
- GitHub Actions: dispatch a pushed revision, view status, logs and outputs.
- Documentation layout: position existing named KiBot groups using board and
  worksheet geometry, while preserving manual placement choices.

Template and standards sources will be configurable. Support both the upstream
KDT template and company templates; branch policies must follow the selected
template. Plugin upgrades and applying new project standards remain separate.
