# Usage and recovery

## Compatibility and sources

KiCad 9 and 10 are supported. Project Setup and template review expect the KiBot layout
described below; design rules and stackup features also work with saved KiCad 9
projects without KiBot configuration. No particular organization’s template or
catalog is required. Templates and standards are separate local folders or Git
checkouts; the plugin does not fetch, commit, push or run CI.

On Overview, select a source with **Choose template…** or **Standards repository →
Change…**. Source paths are remembered on this computer. **Refresh** reloads
project files and the catalog. An installed template is the comparison source;
its version does not establish that it is the latest remote release.

## Template review

A versioned template contains `kibot_resources/project_manager/template.json`.
New KiCad projects may carry a copy of that manifest. Older projects keep an
unknown history until reviewed. Sources with missing, added or modified managed
files must be restored or published as a new version before use; **Details…**
lists the affected paths. Downgrades and switching template identities are refused.

The managed inventory covers KiBot YAML, `.github/workflows/ci.yaml`,
`kibot_launch.sh`, all logos in `Logos/`, drawing sheets in `Templates/`, and supported resources in
`kibot_resources/{fonts,colors,scripts,templates}`. Board, schematic, project
settings, custom rules and extra project files are outside template upgrades.
Logo checks include nested folders, text SVGs and binary image files. Missing
files are reported even when project and installed template versions match;
reviewing **Use template** copies them into the project. Changed project logos
require review before replacement. Template maintainers must regenerate the
manifest with a new template version after adding files.

Maintenance files under `meta/`, including `meta/tests/`, are outside the required
inventory. The plugin manages only `.github/workflows/ci.yaml`; optional workflows
are neither installed nor updated. Existing local maintenance files are left alone.

Templates can also manage `.gitignore` with these exact section markers:

```gitignore
### TEMPLATE IGNORE RULES (managed by Project Manager)
# Shared patterns go here.
### PROJECT IGNORE RULES (add your rules below)
# Your additional patterns and !pattern overrides go here.
```

**Use template** updates only the shared section and preserves everything below
the project marker. When adopting an older, unmarked project `.gitignore`, all
its existing rules are retained in the project section for review. Later project
rules can override shared rules; Git requires ignored parent folders to be
re-included before their children. Older templates without these markers still
work and do not manage `.gitignore`. Customization status compares project
patterns in order, ignoring comments and blank lines. Removing your added rules
returns the row to green without rewriting your explanatory comments.

Templates may also include root `LICENSE` as a reviewed document. It uses the
same file list and **Keep project / Use template** actions as README sources and
notes. Older manifests that did not manage this file remain supported. The
licence text always comes from the selected template.

**Review adoption…** / **Review upgrade…** opens **Project documents** by default.
The left file list contains editable notes, README sources, `LICENSE` and managed
`.gitignore` files. **Template files** switches to shared YAML, scripts, fonts,
logos and other resources. Search filters the active list; the full path appears
above the selected file. Drag the vertical divider to give the editor more space.

Matches appear green, accepted customizations blue, differences needing review
amber and missing files red, with text labels. Matching and accepted files start
at **Keep project**; differences start at **Leave pending**. Shift-click selects
a range; Ctrl-click (Command on Mac) adds files. The action list offers choices
valid for the whole selection: **Keep project**, **Use template**, **Save edits**
for a single editable file, or **Leave pending**. Pending files stay unchanged.

A document opens in **Edit**. Typing stages **Save edits**; nothing is written
until Apply. The four standard sources are `fabrication_notes.txt`,
`assembly_notes.txt`, `impedance_table.txt` and `readme.txt` under
`kibot_resources/templates/`. Templates can declare additional plain documents
in their manifest. For `.gitignore`, edit only the project section; the shared
rules can be expanded for reading in a draggable 50/50 horizontal split. Saving project rules never silently upgrades
an outdated shared section: choose **Use template** for that update.

**Changes → Compare with template** is a reference comparison: red lines exist
only in the saved project, green lines only in the installed template. These
colors describe differences; they do not decide what Apply writes.
**Changes → Changes to apply** follows the selected action: Keep project and
Leave pending do not rewrite the file; Use template and Save edits show their
planned removals/additions. **Preview Apply…** shows all planned writes in color,
including staged document edits and review records.

Drafts and actions survive switching lists, files and search filters. **Refresh**
reloads both the project and installed template. Choices/drafts are retained for
unchanged files; changed files are rechecked. If a changed file has your draft,
refresh asks before discarding that draft. Cancel leaves the review intact.
Stored document baselines still support non-overlapping three-way merge proposals;
they no longer occupy a separate tab. Kept shared-file differences remain visible.

The standard seven-column impedance document opens in **Table**. Edit cells and
use **Add row** / **Delete selected rows**; padding and tabs are unnecessary.
**Text** exposes its comma-separated source. Other headings or malformed CSV keep
the ordinary text editor, so different templates remain usable. Applying saves
the same text document, with CSV quoting and ordinary newlines.

If a standards folder is linked, **Add missing profiles** offers only presets
whose copper/dielectric construction matches the saved PCB. Save board changes
before importing. Import rereads the standards and board, appends missing targets
and preserves existing rows, including manual 50 Ω entries and tolerances. If
an existing target on the same signal/reference layers has a different width or
gap, it stays unchanged and the conflict is reported. Equivalent numeric/layer
spellings do not create duplicate rows. Missing source layers are reported;
missing impedance tolerances remain blank. Guidance does not calculate impedances
or modify routing, netclasses, the PCB, or the standards repository.

Put reusable new targets in that preset's `impedance_targets` in the standards
repo, then Refresh and add missing profiles. No match leaves import disabled;
the manual table remains editable. Multiple matching presets require a selection;
the importer does not offer guidance from different saved-board constructions.

Choose **Keep project → Apply** to accept a custom document. The choice and
reviewed template content are saved in `kibot_resources/project_manager/project.json`;
commit this file with the project so the decision survives reopening and cloning
on another computer. Accepted documents remain visible in blue with their diffs
available. You can continue editing them without another warning. The overall
status stays green and reports the accepted-customization count. When a later
template changes that particular document, it becomes amber for another review;
unrelated template updates do not invalidate your choice. Older accepted document
records keep their previous behaviour when opened by this plugin.

To change your mind, select a blue file and choose **Use template → Apply**.
After an overwrite confirmation and backup, it returns to green. Later edits to
that restored default need a new Keep decision. Editing a document in the review
window and applying it also records an accepted customization. Shared automation
files and the shared `.gitignore` section still flag differences even when kept.

To restore a default README, select `readme.txt` in **Project documents** and
choose **Use template → Apply**. Its previous content is backed up after an
overwrite warning. CI regenerates root `README.md` on its next run; you can edit
the source again afterwards. Missing README sources remain required.

**Apply** saves directly and keeps review open with refreshed results. Repeat it
for another batch. **Preview Apply…** shows the writes; accepting that preview
applies the reviewed plan and keeps review open too. Closing review retains anything already applied. Unapplied drafts stay only in
this window; save them before closing.
Undecided files keep their contents and previous baselines. The recorded template
version identifies the reviewed source; outstanding differences still determine
whether the project matches. Commit the records in
`kibot_resources/project_manager/` with the project.

![Template review with a resizable document editor](screenshots/template-review.png)

## Project Setup

Setup edits `definitions` metadata in `kibot_yaml/kibot_main.yaml` and the workflow’s
`env.kibot_variant` and **KiCad version for CI** (`env.kicad_version`). Authors share the existing `DESIGNER` field. An optional
`REVIEWER` is added only when entered. Missing workflows are added through template
review; workflow status stays unavailable until the file exists.

After **Apply**, a green message confirms that changes were saved and remains
visible after refresh. Save failures show a red message and an error dialog;
your edits remain available to correct or retry. Editing a field clears the
previous outcome. Applying unchanged values shows “No changes to save.”

**DRAFT**, **PRELIMINARY** and **CHECKED** select the workflow’s documentation/check
mode. Version tags control **RELEASED**. KiBot propagates metadata into text
variables and reports on the next run. Saving setup does not run checks or
establish that an electrical design has been verified.

In a KiCad 10 editor, an existing KiCad 9 CI target is suggested as **KiCad 10**.
The message identifies the mismatch; **Apply** saves the selected target into the
workflow. Opening the plugin alone changes no files. A newer CI target is never
automatically downgraded. Template updates preserve this project choice; use
Project Setup to change it. Files saved with KiCad 10 need a KiCad 10 CI target.

Existing values, scalar quoting, comments, newline conventions and branch policies
are preserved where supported. Templates substitute metadata into multiple YAML
contexts, so apostrophes, `colon: space`, comment sequences, macro expressions and
ambiguous YAML scalars are rejected with an explanation. Multiline scalars and
anchors on managed fields are unsupported. Unexpected layouts are rejected.

Template comparison ignores allowed metadata values, workflow status, CI KiCad version and standard
numbered changelog title/body extraction entries; other shared configuration stays
checked. When uncommenting release entries, align `- variable` with the other
list entries and indent `command` two spaces further.

## Rules and stackups

Choose a folder containing `catalog.json`; see [the format](standards-format.md).
Select a preset and inspect its details. Rule comparisons show constraints,
custom rules and missing named netclasses, nets or areas. Applications preserve
project exceptions, schematic net assignments and unrelated settings.

Stackup shows the saved board and selected preset side by side, including
dielectric sublayers and surface properties. Mask, silkscreen and finish overrides
are explicit. A copper layer still used by the board cannot be removed. Matching
uses physical properties rather than preset names. Impedance guides describe
routing geometry; they do not assign nets or change widths.

Save and close both PCB and schematic editors before applying standards, then
reopen. `kpm-standards.json` records source hashes, presets and appearance choices.
Repeated application of unchanged settings makes no further file changes.

**Save as preset…** appears for a saved construction without a catalog match.
Save the board first; editors may remain open for capture. Enter metadata and
optional impedance guides, review duplicate/similar constructions and save one
new JSON file. Saving refuses an existing destination or changed source/catalog.
It refreshes the catalog and selects the new preset without changing the board.

## Backups and troubleshooting

Overview’s **Open Backups** and activity log show backup locations. User settings
and backups are outside the project:

| Platform | Directory |
| --- | --- |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/kicad_project_manager/` |
| macOS | `~/Library/Application Support/kicad_project_manager/` |
| Windows | `%LOCALAPPDATA%\kicad_project_manager\` |

Each backup includes original files and a `manifest.json`. To restore, close the
plugin and editors, review any later changes, then copy originals to their
project-relative paths. An entry with `existed: false` identifies a newly created
file: remove that file when undoing the apply. A failed multi-file save rolls back
its writes when no external edit has intervened.

- **No toolbar button:** enable KiCad API, check Python/wxPython, recreate the
  plugin environment in Preferences → Plugins and restart PCB Editor.
- **Project unavailable:** save/open the board, close modal editor dialogs and Refresh.
- **Changed during review:** Refresh and review again; external edits are preserved.
- **Configuration/workflow missing:** select a compatible versioned template and
  add it through template review. The source picker starts in KiCad’s user template
  folder; `KICAD_USER_TEMPLATE_DIR` overrides that location.
- **Previously installed PCM copy:** uninstall it before installing the generic
  `org.kicadprojectmanager.plugin` package. Existing projects and user backups stay in
  their current locations.
