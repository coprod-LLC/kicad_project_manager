# KiCad 10 development trial on a Mac

Install KiCad **10.0.7** alongside 9 (keep the applications in separate folders,
for example `/Applications/KiCad 9/` and `/Applications/KiCad 10/`). Open 10 once,
then close its editors before setup. Use copied projects: saving in 10 can upgrade
files so that 9 cannot open them.

With your normal GitHub SSH access, run:

```bash
git clone --branch port/kicad10 git@github.com:coprod-LLC/kicad_project_manager.git
cd kicad_project_manager
./setup_port.command
```

The helper clones the other four port branches into `.port-repos/`, or reuses
sibling checkouts already on `port/kicad10`. It links both plugins and the template,
enables the 10 API, selects bundled Python, registers company libraries, and
prefills the template/standards/library folders, adds missing template fonts,
and installs the 10 color theme. It keeps 9 settings and plugin
links unchanged. Existing 10 config changes get timestamped backups. Conflicting
plugin installations or library nicknames produce an actionable error.

Open a **copied project in KiCad 10 PCB Editor** and launch both plugins from
Tools → External Plugins. KiCad installs their Python dependencies on first launch;
that first launch needs internet. Subsequent source edits take effect after closing
and reopening the plugin; keep the symlinks in place. Library Manager is based on
**1.3.10**, including the Unicode filename fix; this port is not a new release.

Database parts need the existing `SQLite3 ODBC Driver` registration. If missing,
the helper uses Homebrew to install/register `sqliteodbc`; install Homebrew first
if setup reports it missing. `./setup_port.command --skip-database` permits a
plugin-only trial. GitHub requests/sync use your normal Git/SSH and plugin token
or `gh auth login` credentials. The trial company library targets **port/kicad10**.

For the first real trial, check these flows:

- Project Manager: read project metadata; preview/apply setup; compare/update the
  template; apply rules/stackups on copies; save a construction as a preset.
- Library Manager: browse/search parts, symbols and footprints; check previews;
  generate a footprint; submit a disposable part request and sync the rebuilt DB.
- In the schematic editor: place a database part, change its data through Library
  Manager, sync, and update the placed symbol from the library.
- Reopen a KiCad 9 project and confirm its old plugin settings still work.

Native macOS UI, Apple Silicon driver loading, Keychain/GitHub authentication,
and schematic database placement still require this Mac trial. The Linux tests
and simulated Mac setup cannot certify those platform-specific flows.

Docker is optional for plugin trials. To reproduce CI outputs locally, install
Docker Desktop and, from a disposable generated project, run:

```bash
python3 kibot_resources/scripts/run_kibot.py --container --major 10 \
  --variant PRELIMINARY --version mac-trial --network bridge
```

The first 10 toolchain build downloads the verified official AppImage and pinned
KiBot engine (several GB; allow roughly 15 GB free disk). Later runs reuse it.
Apple Silicon runs this Linux amd64 image through Docker emulation. The native
plugins themselves use KiCad's bundled Mac Python and do not require Docker.
