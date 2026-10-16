Standards application and project-setup refinements for KiCad 9.

- Compare and apply a local standards catalog. Custom rules update one marked
  company-rules block in `.kicad_dru`. Rules outside that block are kept, and
  later rules still override earlier ones.
- Merge constraint keys and named net classes without replacing net assignments
  or unrelated project settings.
- Update copper, dielectrics, copper finish, and board thickness. Mask, silk,
  and paste stay unless the preset includes them. A copper layer the board still
  uses blocks the apply.
- Record the preset id, source hashes, and Git commit (when the standards folder
  is a checkout) in `kpm-standards.json`.
- Close the PCB and schematic editors before applying standards, then reopen the
  project. A second launch of the plugin warns instead of opening another window.
- On the Overview, Found is green and Not found is red. Restore a missing
  `.github/workflows/ci.yaml` from there; the browser starts in KiCad's user
  template folder.
- Project status choices include what each status generates. Apply from Project
  Setup directly, with Preview Changes still optional.

Applying standards writes saved project files after the editors are closed. It
does not run DRC, commit, push, or approve the electrical design. KiBot still
updates document text variables on the next CI run.

The supported template fields must be single-line scalars. Because KiBot performs
literal text substitution, some punctuation (including apostrophes and
colon-space sequences) requires template changes and is rejected before saving.
