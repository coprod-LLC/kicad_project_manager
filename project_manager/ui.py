"""Native wxPython shell. All project access in this milestone is read-only."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading
from typing import Callable

import wx

from . import NAME, __version__
from .context import ContextError, ProjectContext


class ProjectManagerFrame(wx.Frame):
    def __init__(self, provider: Callable[[], ProjectContext]):
        super().__init__(None, title=f"{NAME} — {__version__}", size=(820, 570))
        self._provider = provider
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
        layout.Add(header, 0, wx.EXPAND | wx.ALL, 16)

        box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Active project")
        grid = wx.FlexGridSizer(cols=2, vgap=12, hgap=16)
        grid.AddGrowableCol(1)
        self.fields: dict[str, wx.TextCtrl] = {}
        for key, label in [
            ("project_name", "Project"), ("project_file", "Project file"),
            ("board_file", "Board file"), ("kicad_version", "KiCad version"),
            ("kibot", "KiBot configuration"), ("workflow", "GitHub workflow"),
        ]:
            grid.Add(wx.StaticText(box.GetStaticBox(), label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            field = wx.TextCtrl(box.GetStaticBox(), value="—", style=wx.TE_READONLY | wx.BORDER_NONE)
            self.fields[key] = field
            grid.Add(field, 1, wx.EXPAND)
        box.Add(grid, 1, wx.EXPAND | wx.ALL, 14)
        layout.Add(box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)

        note = wx.StaticText(panel, label="Preview release: inspect project information. Project editing is planned.")
        note.Wrap(740)
        layout.Add(note, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)
        layout.Add(wx.StaticText(panel, label="Activity"), 0, wx.LEFT | wx.TOP, 16)
        self.log = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 110))
        layout.Add(self.log, 1, wx.EXPAND | wx.ALL, 16)
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
        self.SetMinSize((720, 540))
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Centre()
        wx.CallAfter(self.refresh)

    def _append_log(self, message: str) -> None:
        self.log.AppendText(f"{datetime.now():%H:%M:%S}  {message}\n")

    def refresh(self) -> None:
        if self._busy or self._closing:
            return
        self._busy = True
        self.refresh_button.Disable()
        self.status.SetLabel("Reading the active project…")

        def worker() -> None:
            context, error = None, None
            try:
                context = self._provider()
            except ContextError as exc:
                error = str(exc)
            except Exception:
                error = "Could not inspect the project. Close and reopen the plugin to retry."
            wx.CallAfter(self._finish_refresh, context, error)

        threading.Thread(target=worker, daemon=True, name="project-context").start()

    def _finish_refresh(self, context: ProjectContext | None, error: str | None) -> None:
        if self._closing:
            return
        self._busy = False
        self.refresh_button.Enable()
        self._context = context
        if error or context is None:
            for field in self.fields.values():
                field.ChangeValue("—")
                field.SetToolTip("")
            self.status.SetLabel("Project unavailable — see activity below")
            self._append_log(error or "No project was returned.")
            return
        for key in ("project_name", "project_file", "board_file", "kicad_version"):
            value = getattr(context, key) or "No project file (standalone board)"
            self.fields[key].ChangeValue(value)
            self.fields[key].SetToolTip(value)
        self.fields["kibot"].ChangeValue("Found" if context.kibot_config_present else "Not found")
        self.fields["workflow"].ChangeValue("Found" if context.workflow_present else "Not found")
        self.status.SetLabel("Connected to PCB Editor")
        self._append_log("Project information refreshed.")
        if not context.board_saved:
            self._append_log("The board has not been saved to disk yet.")
        if context.project_file and not context.project_saved:
            self._append_log("The reported project file does not exist on disk yet.")

    def _on_close(self, event: wx.CloseEvent) -> None:
        self._closing = True
        self.Destroy()
