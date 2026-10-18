KiCad Project Manager v0.5.0 adds a visual, manufacturer-oriented standards workflow.

- Browse Design Rules and Stackup in separate tabs. Overview holds the standards repository and backup controls, with one Refresh beside the tabs.
- Inspect current and selected stackups side by side, including every dielectric ply, material, thickness, surface layer, impedance-control flag and finish. Details includes dielectric constants, loss tangents and thickness locks.
- Identify saved constructions from their layer data and show the manufacturer and full stackup code. Surface or setting differences are marked; ambiguous matches are reported.
- Apply beside the preset selector, with compact, always-visible mask, silkscreen and finish overrides. Success is green, open-editor blocks are amber, and other failures are red.
- Support optional manufacturer metadata, recommendation history, nominal and finished thicknesses, copper order weights and impedance-routing guidance in reusable catalogs. Private standards stay in their own repository.
- Review board constraints, custom rules and missing dependencies while keeping schematic net assignments and patterns. Existing shared rule prefixes can be adopted through explicit review, preserving later project exceptions.
- Preserve copper aliases and roles, retain omitted surface layers, and calculate board thickness from the full resulting stackup. Applying keeps local backups, editor-lock checks and external-edit conflict checks.

Validation covers 114 unit tests, native GUI flows at narrow and wide sizes, and official KiCad package schemas. This release remains a Testing preview through PCM. Save the project and close the PCB and schematic editors before applying standards, then reopen the project.
