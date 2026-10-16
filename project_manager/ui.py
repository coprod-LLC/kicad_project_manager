"""Native wxPython IPC companion with project inspection and setup."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading
from typing import Callable

import wx

from . import NAME, __version__
from .context import ContextError, ProjectContext
from .preferences import kicad_user_template_directory, save_template
from .setup import read_setup, suggested_metadata
from .setup_ui import ProjectSetupPanel
from .settings_ui import SettingsDialog
from .standards_ui import StandardsPanel
from .yaml_fields import SetupError

FOUND_COLOUR = wx.Colour(46, 170, 80)
MISSING_COLOUR = wx.Colour(220, 70, 70)


class ProjectManagerFrame(wx.Frame):
    def __init__(self, provider: Callable[[], ProjectContext], storage: Path | None = None):
        super().__init__(None, title=f"{NAME} — {__version__}", size=(980, 840))
        self._provider = provider
        self.storage = storage
        self._closing = False
        self._busy = False
        self._context: ProjectContext | None = None
        icon_path = Path(__file__).resolve().parent.parent / "resources" / "icon.png"
        if icon_path.is_file():
            self.SetIcon(wx.Icon(str(icon_path), wx.BITMAP_TYPE_PNG))

        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.VERTICAL)
        header = wx.BoxSizer(wx.HORIZONTAL)
        self.status = wx.StaticText(panel, label="Reading the active project…")
        header.Add(self.status, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        self.refresh_button = wx.Button(panel, label="Refresh")
        self.refresh_button.Bind(wx.EVT_BUTTON, lambda event: self.refresh())
        header.Add(self.refresh_button)
        self.settings_button = wx.Button(panel, label='Settings…')
        self.settings_button.Bind(wx.EVT_BUTTON, lambda event: self.open_settings())
        header.Add(self.settings_button, 0, wx.LEFT, 8)
        layout.Add(header, 0, wx.EXPAND | wx.ALL, 16)

        self.notebook = wx.Notebook(panel)
        overview = wx.Panel(self.notebook)
        overview_layout = wx.BoxSizer(wx.VERTICAL)
        box = wx.StaticBoxSizer(wx.VERTICAL, overview, "Active project")
        grid = wx.FlexGridSizer(cols=2, vgap=12, hgap=16)
        grid.AddGrowableCol(1)
        self.fields: dict[str, wx.TextCtrl] = {}
        for key, label in [
            ("project_name", "Project"), ("project_file", "Project file"),
            ("board_file", "Board file"), ("kicad_version", "KiCad version"),
            ("kibot", "KiBot configuration"),
        ]:
            grid.Add(wx.StaticText(box.GetStaticBox(), label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            field = wx.TextCtrl(box.GetStaticBox(), value="—", style=wx.TE_READONLY | wx.BORDER_NONE)
            self.fields[key] = field
            grid.Add(field, 1, wx.EXPAND)
        grid.Add(wx.StaticText(box.GetStaticBox(), label="GitHub workflow"), 0, wx.ALIGN_CENTER_VERTICAL)
        workflow_row = wx.BoxSizer(wx.HORIZONTAL)
        field = wx.TextCtrl(box.GetStaticBox(), value="—", style=wx.TE_READONLY | wx.BORDER_NONE)
        self.fields["workflow"] = field
        self._neutral_colour = field.GetForegroundColour()
        workflow_row.Add(field, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        restore_label = "Restore missing .github/workflows/ci.yaml from a template"
        self.restore_workflow_button = wx.Button(box.GetStaticBox(), label=restore_label)
        text_width, _text_height = self.restore_workflow_button.GetTextExtent(restore_label)
        self.restore_workflow_button.SetMinSize((text_width + 48, -1))
        self.restore_workflow_button.SetToolTip("Copy the workflow from a KiCad project template. An existing workflow is never replaced.")
        self.restore_workflow_button.Bind(wx.EVT_BUTTON, self._restore_workflow)
        workflow_row.Add(self.restore_workflow_button, 0, wx.ALIGN_CENTER_VERTICAL)
        workflow_row.Hide(self.restore_workflow_button)
        self._workflow_row = workflow_row
        grid.Add(workflow_row, 1, wx.EXPAND)
        box.Add(grid, 1, wx.EXPAND | wx.ALL, 14)
        overview_layout.Add(box, 0, wx.EXPAND | wx.ALL, 16)

        note = wx.StaticText(overview, label="Project Setup edits the project name, board name, company, authors, Git URL, logo, and documentation status. If the GitHub workflow is missing, restore it here from a KiCad template. Standards applies company rules and stackup after the PCB and schematic editors are closed.")
        note.Wrap(740)
        overview_layout.Add(note, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)
        overview_layout.Add(wx.StaticText(overview, label="Activity"), 0, wx.LEFT | wx.TOP, 16)
        self.log = wx.TextCtrl(overview, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 110))
        overview_layout.Add(self.log, 1, wx.EXPAND | wx.ALL, 16)
        overview.SetSizer(overview_layout)
        self.notebook.AddPage(overview, 'Overview')
        self.setup_panel = ProjectSetupPanel(self.notebook, self._on_applied, storage, self._verify_project)
        self.notebook.AddPage(self.setup_panel, 'Project Setup', select=True)
        self.standards_panel = StandardsPanel(self.notebook, self.open_settings, storage,
                                              self._on_standards_applied, self._verify_standards)
        self.notebook.AddPage(self.standards_panel, 'Standards')
        layout.Add(self.notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)
        footer = wx.BoxSizer(wx.HORIZONTAL)
        footer.Add(wx.StaticText(panel, label=f"Version {__version__} · KiCad 9"), 1, wx.ALIGN_CENTER_VERTICAL)
        close = wx.Button(panel, id=wx.ID_CLOSE)
        close.Bind(wx.EVT_BUTTON, lambda event: self.Close())
        footer.Add(close)
        layout.Add(footer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)
        panel.SetSizer(layout)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer)
        self.SetMinSize((860, 640))
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Centre()
        wx.CallAfter(self.refresh)

    def _append_log(self, message: str) -> None:
        self.log.AppendText(f"{datetime.now():%H:%M:%S}  {message}\n")

    def open_settings(self):
        with SettingsDialog(self, self.storage) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.standards_panel.reload_catalog()

    def refresh(self, discard: bool = False) -> None:
        if self._busy or self._closing:
            return
        if not discard and not self._can_discard():
            return
        self._busy = True
        self.refresh_button.Disable()
        self.status.SetLabel("Reading the active project…")
        self.setup_panel.clear('Reading project setup…')
        self.standards_panel.set_context(None)
        self.standards_panel.reload_catalog()

        def worker() -> None:
            context, error, setup_result = None, None, None
            try:
                context = self._provider()
                if context.project_saved and context.board_saved:
                    try:
                        snapshot = read_setup(Path(context.project_file).parent)
                        values = suggested_metadata(snapshot, context.project_name, Path(context.board_file).stem)
                        setup_result = (snapshot, values, None)
                    except (ValueError, OSError) as exc:
                        setup_result = (None, None, str(exc))
                else:
                    setup_result = (None, None, 'Save a KiCad project and board before editing project setup.')
            except ContextError as exc:
                error = str(exc)
            except Exception:
                error = "Could not inspect the project. Close and reopen the plugin to retry."
            wx.CallAfter(self._finish_refresh, context, error, setup_result)

        threading.Thread(target=worker, daemon=True, name="project-context").start()

    def _finish_refresh(self, context: ProjectContext | None, error: str | None, setup_result=None) -> None:
        if self._closing:
            return
        self._busy = False
        self.refresh_button.Enable()
        self._context = context
        self.standards_panel.set_context(context if not error else None)
        if error or context is None:
            for field in self.fields.values():
                field.ChangeValue("—")
                field.SetForegroundColour(self._neutral_colour)
                field.SetToolTip("")
            self._workflow_row.Hide(self.restore_workflow_button)
            self._workflow_row.Layout()
            self.status.SetLabel("Project unavailable — see activity below")
            self.setup_panel.clear(error or 'Project unavailable. Click Refresh to reconnect.')
            self._append_log(error or "No project was returned.")
            return
        for key in ("project_name", "project_file", "board_file", "kicad_version"):
            value = getattr(context, key) or "No project file (standalone board)"
            self.fields[key].ChangeValue(value)
            self.fields[key].SetToolTip(value)
        self._show_presence("kibot", context.kibot_config_present)
        self._show_presence("workflow", context.workflow_present)
        self._workflow_row.Show(self.restore_workflow_button, not context.workflow_present)
        self._workflow_row.Layout()
        self.Layout()
        self.status.SetLabel(f"Connected — {context.project_name}")
        self._append_log("Project information refreshed.")
        if setup_result:
            snapshot, values, setup_error = setup_result
            if snapshot is not None:
                self.setup_panel.load(snapshot, values)
            else:
                self.setup_panel.clear(setup_error)
                self._append_log(setup_error)
        if not context.board_saved:
            self._append_log("The board has not been saved to disk yet.")
        if context.project_file and not context.project_saved:
            self._append_log("The reported project file does not exist on disk yet.")

    def _show_presence(self, key: str, present: bool) -> None:
        field = self.fields[key]
        field.ChangeValue("Found" if present else "Not found")
        field.SetForegroundColour(FOUND_COLOUR if present else MISSING_COLOUR)
        field.SetToolTip(field.GetValue())

    def _restore_workflow(self, event):
        snapshot = self.setup_panel.snapshot
        if snapshot is None or snapshot.workflow is not None:
            return
        start = kicad_user_template_directory()
        while not start.exists() and start != start.parent:
            start = start.parent
        dialog = wx.DirDialog(self, "Choose the KiCad template that contains .github/workflows/ci.yaml",
                              str(start) if start.exists() else "",
                              style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        selected = dialog.GetPath()
        dialog.Destroy()
        try:
            save_template(selected, self.storage)
            backup = self.setup_panel.restore_workflow(Path(selected))
        except (SetupError, OSError) as exc:
            wx.MessageBox(str(exc), "GitHub workflow", wx.OK | wx.ICON_ERROR, self)
            return
        self._show_presence("workflow", True)
        self._workflow_row.Hide(self.restore_workflow_button)
        self._workflow_row.Layout()
        self.Layout()
        self._append_log(f"Workflow restored. Backup: {backup}" if backup else "The workflow was already present.")

    def _on_applied(self, backup: Path | None):
        self._append_log(f'Project setup saved. Backup: {backup}' if backup else 'No changes were needed.')
        self.refresh(discard=True)

    def _verify_project(self):
        try:
            current = self._provider()
        except ContextError as exc:
            raise SetupError(str(exc)) from exc
        if self._context is None or (current.project_file, current.board_file) != (
                self._context.project_file, self._context.board_file):
            raise SetupError('The active project changed. Refresh before applying these edits.')

    def _verify_standards(self):
        try:
            current = self._provider()
        except ContextError:
            # The editors may already be closed, which is what apply requires. File hashes still guard the write.
            return
        if self._context is None or (current.project_file, current.board_file) != (
                self._context.project_file, self._context.board_file):
            raise SetupError('The active project changed. Refresh before applying these standards.')

    def _on_standards_applied(self, backup: Path | None):
        self._append_log(f'Standards applied. Backup: {backup}' if backup else 'Selected standards were already applied.')

    def _can_discard(self) -> bool:
        if not self.setup_panel.dirty:
            return True
        return wx.MessageBox('Discard your unsaved Project Setup edits?', 'Project Setup',
                             wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) == wx.YES

    def _on_close(self, event: wx.CloseEvent) -> None:
        if event.CanVeto() and not self._can_discard():
            event.Veto()
            return
        self._closing = True
        self.Destroy()
