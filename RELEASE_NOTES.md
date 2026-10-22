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
