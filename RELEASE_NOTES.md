Project setup preview for KiCad 9.

- Edit project name, board name, company, multiple authors, Git URL, and logo path.
- Suggest values from the project and Git while retaining existing metadata.
- Restore a missing `.github/workflows/ci.yaml` from a local Coprod or KDT template.
- Remember the template folder per user, outside the project and public plugin.
- Select DRAFT, PRELIMINARY, or CHECKED; version tags still select RELEASED.
- Preview exact edits, back up original files, detect external changes, and roll
  back a failed save. Existing comments and workflow branch policies are retained.
- Preserve earlier PCM versions and use the newest package description on redeploy.

KiBot updates document text variables on the next CI run. This release does not
change board/project files, run KiBot, commit, push, or apply rules/stackups.

The supported template fields must be single-line scalars. Because KiBot performs
literal text substitution, some punctuation (including apostrophes and
colon-space sequences) requires template changes and is rejected before saving.

Validation includes disposable copies of both templates, setup/rollback tests,
Linux GUI preview/cancel/apply/reload, and cross-platform Python/package checks.
macOS and Windows still need manual KiCad GUI integration testing.
