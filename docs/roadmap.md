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
