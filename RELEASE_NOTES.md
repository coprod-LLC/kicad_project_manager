KiCad Project Manager v0.4.0 adds versioned template checks and reviewed upgrades for KiCad 9.

- Overview opens first and shows the selected template path, project and installed versions, and content status. Details and upgrade review use green for matches, amber for modifications, and red for missing required files.
- Check the actual fonts, themes, scripts, KiBot YAML, documentation resources, drawing sheets and CI workflow. Version equality alone never proves that files match.
- Review upgrades file by file, preserving project metadata, workflow status and numbered release-history entries. Project documentation supports three-way merge proposals and explicit conflict decisions.
- Add missing files, including the GitHub workflow, through template review. Review the final diff before applying, with local backups, external-edit checks and rollback.
- Keep template identity and project baselines under `kibot_resources/project_manager`; workstation-specific source paths stay in local settings. Older projects remain unversioned until adoption review.
- The publishing command synchronizes the template description's version badge with its version record and checks for mismatches.

Template checks work offline against the installed source. Remote release discovery remains planned. This release is distributed as a Testing preview through PCM.
