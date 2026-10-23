"""Small native table editor for the standard impedance CSV document."""
from __future__ import annotations

from pathlib import Path
from typing import Callable
import wx
import wx.grid

from .impedance_table import ImpedanceTable, LABELS, add_missing_guides, matching_guides
from .standards import StandardsError


class ImpedancePanel(wx.Panel):
    def __init__(self, parent, on_changed: Callable[[str], None], standards: Path | None = None,
                 board: Path | None = None):
        super().__init__(parent)
        self.on_changed, self.standards, self.board = on_changed, standards, board
        self.table: ImpedanceTable | None = None
        self._loading = False
        self._guides_loaded = False
        self.presets = ()
        layout = wx.BoxSizer(wx.VERTICAL)
        self.import_panel = wx.Panel(self)
        import_layout = wx.BoxSizer(wx.VERTICAL)
        import_layout.Add(wx.StaticText(self.import_panel, label='Guidance matching the saved board stackup:'), 0, wx.BOTTOM, 4)
        import_row = wx.BoxSizer(wx.HORIZONTAL)
        self.preset_choice = wx.Choice(self.import_panel)
        self.preset_choice.SetMinSize((180, -1))
        import_row.Add(self.preset_choice, 1, wx.RIGHT, 8)
        self.import_button = wx.Button(self.import_panel, label='Add missing profiles')
        self.import_button.Bind(wx.EVT_BUTTON, self._import)
        import_row.Add(self.import_button, 0)
        import_layout.Add(import_row, 0, wx.EXPAND)
        self.import_panel.SetSizer(import_layout)
        self.import_panel.Show(standards is not None)
        layout.Add(self.import_panel, 0, wx.EXPAND | wx.BOTTOM, 8)
        self.grid = wx.grid.Grid(self)
        self.grid.CreateGrid(0, len(LABELS))
        self.grid.SetRowLabelSize(35)
        self.grid.SetColLabelSize(40)
        for column, (label, width) in enumerate(zip(LABELS, (220, 80, 90, 60, 80, 70, 120))):
            self.grid.SetColLabelValue(column, label)
            self.grid.SetColSize(column, width)
        self.grid.Bind(wx.grid.EVT_GRID_CELL_CHANGED, self._changed)
        layout.Add(self.grid, 1, wx.EXPAND)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (('Add row', self._add), ('Delete selected rows', self._delete)):
            button = wx.Button(self, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            buttons.Add(button, 0, wx.RIGHT, 8)
        layout.Add(buttons, 0, wx.TOP, 8)
        self.status = wx.StaticText(self, label='Edit cells directly; no spaces or tabs are needed. Apply saves the document.')
        layout.Add(self.status, 0, wx.EXPAND | wx.TOP, 8)
        self.SetSizer(layout)

    def load(self, table: ImpedanceTable):
        self.table = table
        self._loading = True
        try:
            if self.grid.GetNumberRows():
                self.grid.DeleteRows(0, self.grid.GetNumberRows())
            if table.rows:
                self.grid.AppendRows(len(table.rows))
            for row, values in enumerate(table.rows):
                for column, value in enumerate(values):
                    self.grid.SetCellValue(row, column, value)
        finally:
            self._loading = False
        if not self._guides_loaded:
            self.refresh_guides()

    def refresh_guides(self):
        self._guides_loaded = True
        self.presets = ()
        message = ''
        if self.standards is not None:
            try:
                if self.board is None:
                    raise StandardsError('Save the PCB to import guidance from its matching stackup.')
                self.presets = matching_guides(self.standards, self.board)
                if not self.presets:
                    message = 'No impedance guidance matches the saved board construction. Add guidance in the standards repo, then Refresh.'
            except (StandardsError, OSError) as exc:
                message = str(exc)
        self.preset_choice.Set([preset.name for preset in self.presets])
        self.preset_choice.SetSelection(0 if self.presets else wx.NOT_FOUND)
        self.import_button.Enable(bool(self.presets))
        if message:
            self.status.SetLabel(message)
            self.status.Wrap(max(260, self.GetSize().width))
        self.Layout()

    def flush(self):
        if self.grid.IsCellEditControlEnabled():
            self.grid.SaveEditControlValue()
            self.grid.DisableCellEditControl()

    def value(self) -> str:
        rows = tuple(tuple(self.grid.GetCellValue(row, col) for col in range(len(LABELS)))
                     for row in range(self.grid.GetNumberRows()))
        return ImpedanceTable(self.table.headers, rows).render()

    def _changed(self, event):
        if not self._loading and self.table is not None:
            self.on_changed(self.value())
        if event:
            event.Skip()

    def _add(self, event):
        self.flush()
        self.grid.AppendRows(1)
        self.grid.SetGridCursor(self.grid.GetNumberRows() - 1, 0)
        self.grid.MakeCellVisible(self.grid.GetNumberRows() - 1, 0)
        self._changed(None)

    def _delete(self, event):
        self.flush()
        rows = set(self.grid.GetSelectedRows())
        for top, bottom in zip(self.grid.GetSelectionBlockTopLeft(), self.grid.GetSelectionBlockBottomRight()):
            rows.update(range(top.GetRow(), bottom.GetRow() + 1))
        if not rows and self.grid.GetNumberRows():
            rows.add(self.grid.GetGridCursorRow())
        for row in sorted(rows, reverse=True):
            self.grid.DeleteRows(row, 1)
        self._changed(None)

    def _import(self, event):
        self.flush()
        selection = self.preset_choice.GetSelection()
        if selection == wx.NOT_FOUND:
            return
        selected = self.presets[selection]
        try:
            # Never import stale guidance after the board or source changed.
            fresh = matching_guides(self.standards, self.board)
            preset = next((p for p in fresh if p.id == selected.id), None)
            if preset is None:
                self.refresh_guides()
                raise StandardsError('The saved board or standards changed. Select a matching stackup again.')
            table, added, conflicts = add_missing_guides(ImpedanceTable.parse(self.value()), preset)
            self.load(table)
            if added:
                self.on_changed(table.render())
            message = f'Added {added} profile(s). Existing rows and tolerances kept.'
            if conflicts:
                message += '\n' + '\n'.join(conflicts)
            self.status.SetLabel(message)
            self.status.Wrap(max(260, self.GetSize().width))
            self.status.SetToolTip(message)
            self.Layout()
        except (StandardsError, OSError, ValueError) as exc:
            wx.MessageBox(str(exc), 'Import impedance guidance', wx.OK | wx.ICON_ERROR, self)
