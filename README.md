# KiCad Project Manager

A small, native wxPython IPC companion for KiCad 9 projects and KiBot workflows.

**Current preview: template checks and upgrades, project setup, standards presets, and PCM distribution.**
The window connects to the launching PCB Editor. Project Setup edits KiBot
configuration and workflow files. Standards can update saved design rules and
stackup after the PCB and schematic editors are closed.

GitHub Actions controls are planned. See
[the roadmap](docs/roadmap.md). Public plugin code is independent
of any company's private templates, standards, and PCB repositories.

## Installation with PCM

1. Install **KiCad 9**, and enable **Preferences → Plugins → Enable KiCad API**.
2. On Linux, select a system Python with pip, venv, and wxPython installed. For
   Debian/Ubuntu: `sudo apt install python3-pip python3-venv python3-wxgtk4.0`.
   On macOS/Windows, use KiCad's bundled Python. Other Linux distributions should
   use their equivalent packages. No ODBC driver, KiBot, or Docker is needed.
3. Open **Plugin and Content Manager → Manage…** and add:

   ```text
   https://coprod-llc.github.io/kicad_project_manager/repository.json
   ```

4. Select **coprod KiCad plugins**, install **KiCad Project Manager**, and apply
   pending changes. Preview packages are marked **Testing**.
5. Restart the PCB Editor. Allow KiCad to create the plugin's Python environment;
   the toolbar action may take a minute to appear on its first launch.
6. Open it from the toolbar or **Tools → External Plugins → KiCad Project Manager**.

PCM discovers later releases through the same feed. Alternatively, download the
`kicad_project_manager-X.Y.Z.zip` asset from [Releases](https://github.com/coprod-LLC/kicad_project_manager/releases)
and select **Install from file…**. Installing a newer ZIP with the same identifier
updates the existing installation. Use the packaged release ZIP, not GitHub's
automatically generated source ZIP.

## Project setup

1. Open a saved KiCad 9 project created from the Coprod or KDT KiBot template.
2. The plugin opens on **Overview**. Select **Project Setup** to edit metadata. Existing values are retained;
   template placeholders receive suggestions from project filenames and Git.
3. Edit project name, board name, company, authors (one per line), optional reviewer, Git web URL,
   and logo path. Authors are stored together in the existing `DESIGNER` field.
4. If the GitHub workflow is missing, the template check shows **Missing required files**.
   Choose **Choose template…**, then **Review adoption…** or **Review upgrade…**.
   Add `.github/workflows/ci.yaml` through the same review as other missing files. The folder browser
   opens in KiCad's user template directory (or the nearest existing folder).
   The selected folder must contain a valid versioned template manifest. An existing
   workflow is replaced only when explicitly selected in template review. A newly
   added workflow keeps the template's status and branch policy.
5. Select **DRAFT**, **PRELIMINARY**, or **CHECKED**. This changes
   `env.kibot_variant`; **RELEASED** remains controlled by version tags.
   Status stays unavailable until the workflow exists.
6. Choose **Apply** to save directly, or **Preview Changes…** to review the diff
   before choosing **Apply Changes**. Both paths validate changes and create backups.

The editable files are `kibot_yaml/kibot_main.yaml` and
`.github/workflows/ci.yaml`. Comments, unrelated settings, line endings, and the
selected template's branch policies are preserved. Edited values keep their plain,
single-quoted, or double-quoted style where safe. Quotes added around text by older
versions are valid YAML and do not become part of the value; they need no cleanup.
Unexpected or ambiguous YAML
layouts are rejected rather than rewritten. If another program changes either
file while the form is open, Refresh and review again. The active editor is also
checked immediately before applying changes.

The optional Reviewer field writes `REVIEWER` in `kibot_main.yaml`. Older projects
without the key still load and remain unchanged until a reviewer is entered.
Upgrade their template automation and drawing sheets to display the value.
Empty reviewer values use `''`; ordinary names entered through Project Setup are
saved without quotes. Reviewer names are preserved during template upgrades,
like authors and company.

KiBot propagates metadata into project text variables and generated documents on
the next CI run. Saving setup here does not commit, push, trigger Actions, or mark
an electrical design as verified. The status chooses which CI checks/outputs run.
The supported templates do not require CHECKED before a release: a matching
version tag selects RELEASED, which runs ERC/DRC as part of generating outputs.

**Template syntax limitation:** these templates substitute metadata as literal
text into both plain and single-quoted YAML. New values containing apostrophes,
`colon: space`, comment sequences, macro expressions, or ambiguous YAML scalars
are rejected with an explanation. Encoding a value in `definitions` alone cannot
fix those uses. Unicode names, spaces, multiple authors, and ordinary URLs work.
Multiline/block scalars and YAML anchors on managed fields are unsupported.

Settings and backups are local to the user, outside the project and plugin:

| Platform | Settings and backups directory |
| --- | --- |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/kicad_project_manager/` |
| macOS | `~/Library/Application Support/kicad_project_manager/` |
| Windows | `%LOCALAPPDATA%\kicad_project_manager\` |

The Overview activity log shows each backup location. Each backup preserves the
original files and a `manifest.json`; a manifest entry with `existed: false` means
the workflow was newly created. To undo an apply, close the plugin, copy the
original files back to their project-relative paths, and remove a newly created
workflow if appropriate. Review later edits before restoring. An interrupted
multi-file save automatically restores files it wrote when no external edit has
intervened.

## Template source and upgrades

The Overview shows the selected local template path, the project's recorded
template version, and the installed template version. **Choose template…** and
**Change…** select a source without modifying the project. Paths are remembered
on this computer by template identity; absolute workstation paths are never
written into the project. A project with a known identity can find its source
automatically when exactly one matching template is installed in the user template folder.

Templates carry a tracked manifest at
`kibot_resources/project_manager/template.json`. It is copied into new KiCad
projects outside the excluded `meta` folder, keeping project roots clear. Older
projects remain **Project version unknown** until reviewed; selecting a template
does not establish their history.

The check verifies the required inventory and file contents, including fonts,
themes, scripts, KiBot YAML, documentation resources, drawing sheets, the launch
script and `.github/workflows/ci.yaml`. A version number alone never proves a match.
Missing required files are red; incoming updates or unexpected modifications are
amber; a verified match is green. Text accompanies every color. **Details…** lists
the result for each file and additional project files that will be preserved.
Installed system fonts are outside this check.
Both Details and the upgrade-review file list use green for matches, amber for
modified/incoming content, and red for missing required files. Status text remains
visible alongside the colors.

The Overview is the opening tab. The template label aligns with its source path;
status text wraps to the available column width when the window is resized.
If the installed template has been edited, the Overview explains that the source
must be restored or published as a new version before project checks can run.
**Details…** stays available and lists the changed files and recovery guidance.
There is no separate workflow-presence row or standalone workflow-restore action;
missing workflows are handled by template review.

Only seven metadata values in `kibot_main.yaml`, the workflow's `env.kibot_variant`,
and numbered changelog title/body entries using the standard extraction commands
are excluded from shared-content comparisons. All other main configuration
settings and the shared text-variable commands remain checked. Text line endings
are normalized; editable scalar quoting, comments and blank lines surrounding
the project entries do not cause false differences. Unrecognized YAML layouts
are rejected rather than guessed at.

When enabling commented changelog entries, remove the comment marker and its
following space so the `- variable` lines align with the other list items, with
`command` indented two spaces further. Adding the standard numbered release
entries is supported; malformed indentation or changed extraction commands are
not ignored as valid project customization.

**Review adoption…** / **Review upgrade…** offers per-file decisions. Missing
files and shared files unchanged since the previous baseline can be updated;
unexpected modifications require an explicit keep/replace decision. The four
project documentation sources (`fabrication_notes.txt`, `assembly_notes.txt`,
`impedance_table.txt`, `readme.txt`) preserve project customization. If both sides
changed, non-overlapping edits receive a three-way merge proposal. Overlapping
edits require a decision, with the previous baseline, incoming diff and an editor
available in the review dialog. Keeping a changed document explicitly acknowledges
that release's incoming changes; kept shared-file exceptions still show as modified.

The final preview shows the actual writes before **Apply Changes**. Originals use
the existing local backup/rollback mechanism. Every compared file and the source
template are checked again before writing. Successful review records the baseline
at `kibot_resources/project_manager/project.json`, which should be committed with
the project. Repeating the same apply makes no further changes. Additional files
are retained, including files no longer required by a newer template; remove an
obsolete file separately only after reviewing its use. Downgrades are refused.
Board, schematic, `.kicad_pro` and custom rules are outside template upgrades.

This comparison is against the **installed** source, not a claim about the latest
remote release. Update that checkout or install a newer template release, then
Refresh. Template source updates never automatically upgrade projects. No GitHub
API token or network access is required for these checks.

### Publishing template manifests

From a Project Manager development checkout, generate a manifest for a reviewed
template release, then verify it. First add a visible version badge to the
template's `meta/info.html` with these markers (inside the desired formatting):

```html
<!-- kpm:version:start -->Template v1.0.0<!-- kpm:version:end -->
```

```sh
python -m scripts.template_manifest --template /path/to/template --id example.kicad-kibot --version 1.0.0
python -m scripts.template_manifest --template /path/to/template --check
```

Commit the manifest with the template. Any change to a managed source file requires
regenerating it; the plugin rejects a source with missing, extra or modified managed
files relative to its manifest. Generation updates only the marked HTML badge to
the manifest version, preserving the rest of the description. `--check` also
verifies that the visible badge matches; missing, duplicated, reversed or stale
markers fail validation. The badge is static HTML for KiCad's template selector.
Increment the template version for each new published
release, and keep published tags immutable. The manifest format version is separate
from the template release version. Company data and manifests stay in their own
repositories; public tests contain synthetic templates only.

## Standards

1. On **Overview**, use **Standards repository → Change…** to select a folder
   containing `catalog.json` (a plain folder or existing Git checkout). Its path,
   catalog name and profile counts appear beside the template source. It is remembered on this computer.
   Overview also shows the backup folder, with an **Open Backups** button.
   **Refresh**, beside the four tabs, refreshes the project and reloads the catalog.
2. **Design Rules** shows a descriptive profile, current/selected board constraints,
   and a separate custom-rule list. Missing netclasses, named areas and named nets
   are reported when comparing. Schematic assignments and netclass patterns stay.
   **Profile details…** includes the purpose, assumptions and full custom-rule text.
   Missing dependencies require reviewing the prepared changes even when using Apply.
3. **Stackup** shows **Current saved board** and **Selected preset — preview** side
   by side. Selection changes the preview; it does not change the board. Each
   saved construction is matched against the library's copper and dielectric
   properties, displaying **manufacturer · full construction code** when identified.
   Surface or editor-setting differences are marked; ambiguous matches are reported
   rather than assigning a supplier. Matching uses saved layer data, independently
   of the last applied preset record. Each dielectric sublayer has its own row.
   Dimensions are explicit and the drawing is
   schematic; changed rows have an asterisk and shading. **Details…** exposes all
   materials, thicknesses, colors, dielectric constants, loss tangents and locks.
   Manufacturer codes, nominal copper weights, dated recommendation flags, usage
   history, and optional impedance routing guidance appear in **Preset details…**.
   A compact, always-visible row lets you override mask color, silkscreen color and finish.
4. Save the project in KiCad. For **Stackup**, select a preset, inspect the drawings,
   then choose **Apply** beside the preset selector. Validation and backups run automatically; a short status
   message reports the result: green for success, amber when open KiCad editors block
   applying, and red for other failures. **Design Rules** also offers **Compare with Saved
   Project** and **Review Changes…** for its constraints and custom rules.
5. Close the PCB editor and the schematic editor before applying, and leave this
   window open. Reopen the project afterward. An editor that is still open will
   overwrite the apply the next time it saves. Opening the plugin again while its
   window is already open shows a warning instead of a second window.

Custom rules are not replaced as a whole file. The plugin owns one marked block:

```text
# kicad_project_manager: begin company rules
# kicad_project_manager: end company rules
```

Rules outside that block, including courtyard overrides and voltage domains, are
left untouched. KiCad gives later rules priority, so a project rule below the
block overrides the company rule. The first apply inserts the block just after
`(version 1)`, which puts existing project rules after it. A later apply updates
the block where it already stands.

An existing prefix with the same shared rule names and order can be adopted into
that block through Review Changes. Changed rules are listed and their exact diff
must be reviewed; later project exceptions stay outside the block. Interleaved
local rules are refused. Broken markers from an earlier apply must still be
restored explicitly. The non-UI planning API requires explicit opt-in before
proposing adoption of differing rule values.

Constraints update matching keys in `board.design_settings.rules`. Legacy presets
can also supply netclass definitions, merged by name; assignments, patterns and
other classes stay. Stackup updates copper, dielectric plies, impedance control,
finish and any included silk/paste/mask layers. Omitted physical entries are
retained, and the applied board thickness is calculated from the resulting full
stackup, including retained masks. Existing copper layer roles and display names
stay. A copper layer the board still uses is not removed.

Stackups are maintained in the standards repository. Manufacturer recommendation,
internal preference and prior use are independent metadata;
nominal order thickness and manufacturer finished thickness are distinct from
KiCad's calculated physical total. Routing guidance does not assign nets or set widths.

Each apply records the preset id, source file hashes, and the standards checkout
commit when that folder is a Git repository, plus explicit appearance overrides, in `kpm-standards.json` next to the
project. That file is the provenance for a later drift check. Originals are
backed up with the same restore path as project setup. A second apply of the
same presets makes no further file changes. Company presets are not part of this
public repository.

## Local development

The installation approach follows [KiCad Library Manager](https://github.com/nguyen-v/kicad_library_manager):
`plugin.json`, a Python entrypoint, a KiCad-managed virtual environment, wxPython,
and a symlink to the complete checkout. This project is a separate implementation.

Clone anywhere you develop software, then run:

```sh
git clone https://github.com/coprod-LLC/kicad_project_manager.git
cd kicad_project_manager
python3 scripts/install_dev.py
```

The helper creates a link in the default KiCad 9 IPC plugins directory. It refuses
to replace an existing directory or detected PCM installation. For a customized
KiCad user-data location, pass `--plugins-dir /your/9.0/plugins`.

Typical directories:

| Platform | IPC plugins directory |
| --- | --- |
| Linux | `~/.local/share/kicad/9.0/plugins/` |
| macOS | `~/Documents/KiCad/9.0/plugins/` |
| Windows | `%USERPROFILE%\Documents\KiCad\9.0\plugins\` |

These are **IPC** plugins, so use `plugins/`, not `scripting/plugins/`. On Windows,
enable Developer Mode for symlinks or copy this folder into the IPC plugins
directory. Remove a PCM installation before using the same plugin as a development
checkout; avoid installing both at once.

Restart PCB Editor after initial installation or changes to `plugin.json`.
For ordinary Python edits, close and relaunch the plugin window. When dependencies
change, use **Recreate Plugin Environment** from the plugin's context menu in
Preferences → Plugins. This plugin has its own environment; it does not share or
modify the library manager's environment.

To run from an IDE, use this plugin's KiCad-created Python environment and specify
the intended editor's IPC socket explicitly:

```sh
python run_project_manager.py --socket ipc:///tmp/kicad/api.sock
python run_project_manager.py --socket ipc:///tmp/kicad/api.sock --diagnose
```

That socket is a Linux example. Use the actual endpoint for your editor/platform.
Normal toolbar launches pass the socket and session token automatically. A manual
launch deliberately does not guess which open editor you intended. `--diagnose`
prints local file paths; review that output before sharing it publicly.

## Checks and packaging

In a development virtual environment:

```sh
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m scripts.build_release --tag v0.5.0
```

The ZIP uses an explicit file allowlist. Tests verify archive contents, entrypoint
and icons, deterministic builds, checksums, version matching, and feed history.
On Linux with KiCad's schemas installed:

```sh
python -m scripts.validate_package --schemas /usr/share/kicad/schemas
python -m scripts.smoke_gui --output .dev/gui.png
```

CI runs Python/package checks on Linux, macOS, and Windows, and a native wx GUI
test under Xvfb on Linux. Setup tests cover preservation, multiple authors,
workflow restoration, conflicts, rollback, and repeated applies. A separate
GitHub Actions job uses a pinned public KDT template and the official KiCad 9
KiBot image to verify project text variables and a generated report. No private
template/project files are published in test fixtures or CI artifacts.
Those checks do not replace a manual KiCad/PCM
installation test on each operating system.

## Publishing a release

Use short feature branches and pull requests into `main`. To release:

1. Update `project_manager/__init__.py` and `RELEASE_NOTES.md` in a reviewed commit.
2. Wait for Checks to pass.
3. Tag that commit with `vMAJOR.MINOR.PATCH` and push the tag.

```sh
git tag v0.5.0
git push origin v0.5.0
```

The release workflow checks out the exact tag, tests and packages it, publishes a
GitHub prerelease with its ZIP, metadata and SHA256SUMS, and deploys the PCM feed
through GitHub Pages. Each package is initially marked `testing`. Older published
versions remain available. Existing release assets are never silently overwritten.

Repository setup: enable GitHub Pages with **GitHub Actions** as the publishing
source, and allow the `github-pages` environment to deploy release tags. The
workflow needs `contents: write` for releases and `pages: write` / `id-token: write`
for deployment; no personal access token is stored in the repository. A failed
Pages deployment can be retried through the workflow's manual tag input.

## Troubleshooting

- **No toolbar action:** enable the API; check the Python interpreter and wxPython;
  recreate the plugin environment, then restart the PCB Editor.
- **Project unavailable:** close modal dialogs, save/open a board, and click Refresh.
- **KiCad 10+:** this initial release intentionally supports KiCad 9 only.
- **Missing KiBot configuration:** select a compatible versioned template and use
  template review to add missing automation files. Design files are outside this repair.
- **Missing CI workflow:** on the Overview, select a versioned template with
  **Choose template…**, then add the workflow through **Review adoption…** or
  **Review upgrade…**. The browser starts in KiCad's user template folder:
  `~/.local/share/kicad/9.0/template` on Linux, `~/Documents/KiCad/9.0/template` on macOS,
  and `%USERPROFILE%\\Documents\\KiCad\\9.0\\template` on Windows. `KICAD_USER_TEMPLATE_DIR`
  overrides that location.
- **Official PCM feed errors:** if `repository.kicad.org` is unavailable, KiCad 9
  can use the [direct official v1 feed](https://gitlab.com/kicad/addons/repository/-/raw/main/repository-v1.json).
  The Project Manager feed above is separate.

MIT licensed. The design and installation conventions are inspired by KiCad
Library Manager; the PCB documentation workflow is based on
[KDT_Hierarchical_KiBot](https://github.com/nguyen-v/KDT_Hierarchical_KiBot).
