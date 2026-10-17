KiCad Project Manager v0.4.1 adds reviewer metadata to Project Setup.

- Leave Reviewer blank until a reviewer is assigned. Empty values use an empty YAML string; ordinary reviewer names are saved without quotes.
- Open older projects that have no REVIEWER key without changing their configuration until a name is entered.
- Preserve the reviewer during template comparisons and upgrades, alongside the other project metadata.
- Use Coprod template 1.0.1 to propagate the reviewer through KiBot into schematic and PCB drawing sheets.

Validation covers reviewer entry and clearing, older configurations, Unicode names, template upgrades, and the native GUI. This release remains a Testing preview through PCM.
