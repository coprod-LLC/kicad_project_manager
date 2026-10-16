Initial preview: plugin shell and release distribution.

- Native wxPython window launched from KiCad 9's PCB Editor.
- Read-only project and board detection using the launching editor's IPC session.
- Plugin/KiCad version display, KiBot/CI presence checks, refresh, and actionable connection errors.
- Development symlink helper and cross-platform setup documentation.
- Versioned PCM packages, checksums, and a public update feed.

Project editing, rules/stackup application, and KiBot execution are not included
in this preview. Runtime GUI validation is currently on Linux; macOS and Windows
have automated Python/package checks and need manual KiCad integration testing.
