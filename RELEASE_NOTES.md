KiCad Project Manager v1.1.0

- Separate Project documents from shared Template files in template review. Use
  a resizable file list and editor/diff workspace with search and bulk actions.
- Open editable documents directly in a large editor. Keep drafts and pending
  choices across filters, selections and partial applies. Remove the redundant
  README shortcut and previous-baseline tab; retain stored merge baselines.
- Distinguish reference comparisons from action-specific planned changes. Color
  both diff modes and Apply previews red/green; shared files stay diff-only.
- Refresh the open review from disk without losing compatible drafts. Confirm
  before discarding edits to externally changed files; recheck changed actions.
- Keep review open after confirming Preview Apply, just as with direct Apply.
  Return .gitignore to green after custom patterns are removed; comments and
  blank lines alone no longer count as custom rules.
- Give shared/project ignore sections a draggable horizontal 50/50 split.
- Edit standard impedance CSV documents in a cell table with Add/Delete row and
  Text fallback. Import missing guidance only from a saved-board construction
  match, retaining manual rows/tolerances and reporting width/gap conflicts.
- Simplify Overview by removing the redundant Board file path row.
- Edit only the project section of a managed .gitignore; protect shared rules,
  preserve project overrides and show custom project rules in blue.
- Remember accepted document customizations in the project review record. Keep
  the overall status green and request review when that template default changes.
- Review template-provided LICENSE files through Keep project / Use template,
  with overwrite confirmation and local backups. Older records remain supported.
- Allow template manifests to declare additional plain documents under their
  templates resource folder without enabling editing for automation or binaries.

KiCad Project Manager v1.0.1

- Prevent four GTK allocation warnings when opening Design Rules by sizing its
  nested notebooks before adding their pages. Settings and project files are unchanged.

KiCad Project Manager v1.0.0

- First stable PCM release, supporting KiCad 9 and 10 on Linux, macOS and Windows.
- Support KiCad 9 and 10 IPC, workflows and their explicitly recognized board formats.
- Preserve a project's selected KiCad major during template updates.
- Let Project Setup select the CI KiCad major and suggest upgrading it for files
  opened in KiCad 10. Show persistent success/failure feedback after Apply.
- Require standards to declare compatibility before applying them to another major.
- Read KiCad 10 net names and retain the source major when capturing stackup presets.
- Select template/install paths for the intended major, including documents-root overrides.
- Detect and restore missing template resources, including all nested text/binary
  logos, even when the template version already matches.
- Exclude template maintenance files and optional workflows from managed updates.
- Remove company-specific trial setup and companion-plugin CI dependencies;
  retain generic PCM and manual development installation.

KiCad Project Manager v0.7.0

- Review template files in batches with Shift/Ctrl selection and partial apply.
- Apply directly beside Preview Apply and continue in the open review window.
- Store each stackup preset in one self-contained JSON file under catalog schema 2.
- Save an unmatched saved-board construction as a preset, with metadata,
  impedance guides and duplicate/similarity checks.
- Use a compact README with native screenshots and linked operating/developer guides.
- Use neutral public wording and the runtime-valid package ID
  `org.kicadprojectmanager.plugin`. Validate the extra KiCad 9 identifier constraint
  during development installation, packaging and schema checks.

Uninstall a PCM copy with the previous identity before installing this package.
Existing project data, settings and backups keep their current locations.
Save and close PCB/schematic editors before applying standards; reopen afterward.
