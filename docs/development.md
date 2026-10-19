# Development and releases

For contributors working from a checkout. See the [README](../README.md) for
installation and the [usage guide](usage.md) for everyday operation.

## Development installation

```sh
git clone https://github.com/coprod-LLC/kicad_project_manager.git
cd kicad_project_manager
python3 scripts/install_dev.py --kicad-major 10
```

The helper links this checkout into the selected KiCad major’s IPC `plugins/` folder and refuses
to replace an existing installation. Uninstall PCM copies first, including
packages with earlier identities. Use `--plugins-dir` for a custom location.
On Windows, enable Developer Mode for symlinks or copy the complete checkout.
Use `plugins/`, not `scripting/plugins/`: the latter is for the older SWIG plugin
system. PCB Editor’s **Open Plugin Directory** command can open that older folder.

| Platform | Default plugin directory |
| --- | --- |
| Linux | `~/.local/share/kicad/9.0/plugins/` |
| macOS | `~/Documents/KiCad/9.0/plugins/` |
| Windows | `%USERPROFILE%\Documents\KiCad\9.0\plugins\` |

Restart PCB Editor after installation or a `plugin.json` change. For ordinary
Python edits, close and relaunch the plugin. Dependency changes require
**Recreate Plugin Environment** in Preferences → Plugins. Linux uses a system
Python with pip, venv and distro wxPython; do not pip-build wxPython.

IDE launches must supply the intended editor endpoint explicitly:

```sh
python run_project_manager.py --socket ipc:///tmp/kicad/api.sock
python run_project_manager.py --socket ipc:///tmp/kicad/api.sock --diagnose
```

Use the actual endpoint for your platform. Toolbar launches pass the socket and
session token automatically. Diagnostics contain local paths.

## Checks

```sh
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m scripts.build_release --tag v0.8.0
python -m scripts.validate_package --schemas /usr/share/kicad/schemas
python -m scripts.smoke_gui --output .dev/gui.png
python -m scripts.smoke_stackup_save --output .dev/save-preset
```

GUI checks need native wxPython and a display; CI runs them with Xvfb. Python
checks run on Linux, macOS and Windows. A separate pinned public-template/KiBot
integration verifies metadata and generated documentation. Test fixtures and
screenshots use synthetic projects and standards. Platform installation and
save/apply/reopen checks in KiCad remain useful before releases.

Generate the README screenshots from the actual four-page interface:

```sh
python -m scripts.capture_screenshots --output docs/screenshots
```

`project_manager/` contains runtime code; `tests/` contains regression fixtures
and checks. `scripts/` provides installation, manifest creation, GUI/integration
checks and packaging. `.github/workflows/` runs validation and releases.
`pcm/metadata.json` and `resources/icon.png` support distribution. `.dev/`,
`.venv/`, `dist/`, `site/` and Python caches are ignored local/generated content.
They are excluded from release payloads.

## Template manifests

A template requires compatible KiBot configuration and workflow files. Add a
visible version badge in `meta/info.html`:

```html
<!-- kpm:version:start -->Template v1.0.0<!-- kpm:version:end -->
```

Then generate and verify its managed-file manifest:

```sh
python -m scripts.template_manifest --template /path/to/template --id example.kibot --version 1.0.0
python -m scripts.template_manifest --template /path/to/template --check
```

Generation updates the marked badge and writes
`kibot_resources/project_manager/template.json`. Commit both. Publish any changed
managed source as a new version; published versions are immutable.

## Releases

Update `project_manager/__init__.py` and `RELEASE_NOTES.md` in a reviewed commit.
After Checks pass, tag that commit `vMAJOR.MINOR.PATCH` and push the tag. The
release workflow builds the explicit public-file allowlist, validates official
KiCad schemas, publishes immutable prerelease assets and rebuilds the PCM feed.
Enable GitHub Pages with **GitHub Actions** as its source and permit release-tag
deployments to the `github-pages` environment. A failed deployment can be retried
with the workflow’s manual tag input; existing assets are never overwritten.

The package ID is `org.kicadprojectmanager.plugin` in both IPC and PCM metadata.
KiCad 9 also checks identifiers at runtime: they need at least three dot-separated
parts, beyond what the JSON schema enforces. The development installer and package
builder validate that constraint before installation or packaging.
Earlier package identities require uninstall/reinstall; their immutable ZIPs
remain in GitHub Releases. The new feed retains versions of the generic identity.
Repository URLs are derived from PCM metadata, and release workflows can override
the repository for a fork.
