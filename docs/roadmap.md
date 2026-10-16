# Roadmap

The first milestone is limited to the read-only IPC shell and public release
distribution. No preset application or project-editing code is included.

## Next: step 3 feasibility prototype (not implemented)

Before implementing the standards browser, establish a safe KiCad 9 application
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

Deliverable: a tested recommendation for the application mechanism and its user
interaction, plus evidence of preserved project data. Review it before building
the full standards feature.

## Later increments

- Complete project setup: recover missing template dotfiles, edit metadata and
  authors, synchronize text variables, and select DRAFT/PRELIMINARY/CHECKED.
- Private standards catalog: compatible rules/stackup choices, pinned source
  commits, drift detection, previews, and restoration.
- GitHub Actions: dispatch a pushed revision, view status, logs and outputs.
- Documentation layout: position existing named KiBot groups using board and
  worksheet geometry, while preserving manual placement choices.

Template and standards sources will be configurable. Support both the upstream
KDT template and company templates; branch policies must follow the selected
template. Plugin upgrades and applying new project standards remain separate.
