# KiCad Project Manager

A small, native wxPython IPC companion for KiCad 9 projects and KiBot workflows.

**Current preview: project setup, metadata editing, and PCM release distribution.**
The window connects to the launching PCB Editor and offers an Overview and a
Project Setup form. It edits KiBot configuration and workflow files after an
explicit preview; it does not modify the board or KiCad project file.

Rules/stackup presets and GitHub Actions controls are planned. See
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
2. Open **KiCad Project Manager → Project Setup**. Existing values are retained;
   template placeholders receive suggestions from project filenames and Git.
3. Edit project name, board name, company, authors (one per line), Git web URL,
   and logo path. Authors are stored together in the existing `DESIGNER` field.
4. If the workflow is missing, select **Restore missing workflow** and browse to
   your local template checkout. The folder is remembered on this computer.
   An existing workflow is never replaced by the template.
5. Select **DRAFT**, **PRELIMINARY**, or **CHECKED**. This changes
   `env.kibot_variant`; **RELEASED** remains controlled by version tags.
6. Choose **Preview Changes…**, review the diff, then **Apply Changes**.

The editable files are `kibot_yaml/kibot_main.yaml` and
`.github/workflows/ci.yaml`. Comments, unrelated settings, line endings, and the
selected template's branch policies are preserved. Unexpected or ambiguous YAML
layouts are rejected rather than rewritten. If another program changes either
file while the form is open, Refresh and review again. The active editor is also
checked immediately before applying changes.

KiBot propagates metadata into project text variables and generated documents on
the next CI run. Saving setup here does not commit, push, trigger Actions, or mark
an electrical design as verified. The status chooses which CI checks/outputs run.

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
python -m scripts.build_release --tag v0.2.0
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
git tag v0.2.0
git push origin v0.2.0
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
- **Missing KiBot configuration:** start with a compatible Coprod or KDT template
  project. The plugin repairs a missing workflow, not an entire missing template.
- **Missing CI workflow:** select your local template folder in Project Setup.
- **Official PCM feed errors:** if `repository.kicad.org` is unavailable, KiCad 9
  can use the [direct official v1 feed](https://gitlab.com/kicad/addons/repository/-/raw/main/repository-v1.json).
  The Project Manager feed above is separate.

MIT licensed. The design and installation conventions are inspired by KiCad
Library Manager; the PCB documentation workflow is based on
[KDT_Hierarchical_KiBot](https://github.com/nguyen-v/KDT_Hierarchical_KiBot).
