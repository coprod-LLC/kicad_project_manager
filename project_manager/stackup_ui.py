"""Native physical stackup diagrams and property tables."""
from __future__ import annotations

import wx

from .stackup import PhysicalStackup, physical_summary


def _fit(dc, text, width):
    if dc.GetTextExtent(text).width <= width:
        return text
    while text and dc.GetTextExtent(text + '…').width > width:
        text = text[:-1]
    return text + '…' if text else ''


class StackupDrawing(wx.ScrolledWindow):
    ROW = 24

    def __init__(self, parent):
        super().__init__(parent, style=wx.VSCROLL | wx.BORDER_SIMPLE)
        self.stack: PhysicalStackup | None = None
        self.reference: PhysicalStackup | None = None
        self.SetMinSize((1, 180))
        self.SetScrollRate(0, self.ROW)
        self.ShowScrollbars(wx.SHOW_SB_NEVER, wx.SHOW_SB_ALWAYS)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self._paint)
        self.Bind(wx.EVT_SIZE, lambda event: (self.Refresh(), event.Skip()))

    def set_stackup(self, stack, reference=None):
        self.stack, self.reference = stack, reference
        self.SetVirtualSize((1, max(1, len(stack.layers) * self.ROW + 30) if stack else 100))
        if stack:
            self.SetName('\n'.join(f'{row.label}: {row.kind}, {row.material or "material unspecified"}, '
                                   f'{row.thickness_mm:g} mm' if row.thickness_mm is not None else
                                   f'{row.label}: {row.kind}, {row.material or "material unspecified"}'
                                   for row in stack.layers))
        else:
            self.SetName('No physical stackup available')
        self.Refresh()

    def _paint(self, _event):
        dc = wx.AutoBufferedPaintDC(self)
        background = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
        foreground = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT)
        dc.SetBackground(wx.Brush(background))
        dc.Clear()
        self.PrepareDC(dc)
        dc.SetFont(self.GetFont())
        dc.SetTextForeground(foreground)
        width = self.GetClientSize().width
        if not self.stack:
            dc.DrawText(_fit(dc, 'No physical stackup saved', width - 20), 10, 12)
            return
        reference = {row.key: row for row in self.reference.layers} if self.reference else None
        dark = sum(background.Get()[:3]) < 384
        shade = wx.Colour(78, 66, 39) if dark else wx.Colour(255, 242, 213)
        border = wx.SystemSettings.GetColour(wx.SYS_COLOUR_3DSHADOW)
        for index, layer in enumerate(self.stack.layers):
            y = 10 + index * self.ROW
            changed = reference is not None and reference.get(layer.key) != layer
            if changed:
                dc.SetPen(wx.TRANSPARENT_PEN)
                dc.SetBrush(wx.Brush(shade))
                dc.DrawRectangle(0, y - 4, width, self.ROW)
            if layer.kind == 'copper':
                color, height = wx.Colour(195, 150, 45), 5
            elif layer.kind in ('core', 'prepreg'):
                color = wx.Colour(130, 149, 97) if layer.kind == 'prepreg' else wx.Colour(165, 173, 119)
                height = 16
            else:
                color, height = wx.Colour(layer.color) if layer.color else wx.Colour(160, 160, 160), 4
                if not color.IsOk():
                    color = wx.Colour(160, 160, 160)
            dc.SetPen(wx.Pen(border, 1) if layer.kind not in ('copper', 'core', 'prepreg') else wx.TRANSPARENT_PEN)
            dc.SetBrush(wx.Brush(color))
            dc.DrawRectangle(8, y + (24 - height) // 2, 35, height)
            dc.SetTextForeground(foreground)
            name = ('* ' if changed else '') + layer.label
            dc.DrawText(_fit(dc, name, 92), 52, y)
            material_width = max(1, width - 230)
            material = ' · '.join(value for value in (layer.color, layer.material) if value)
            if layer.kind == 'copper':
                material = material or 'Copper'
            elif layer.kind in ('core', 'prepreg'):
                material = layer.kind + ' · ' + (material or 'Unspecified material')
            else:
                material = material or layer.kind.replace('Top ', '').replace('Bottom ', '')
            dc.DrawText(_fit(dc, material, material_width), 150, y)
            thickness = '—' if layer.thickness_mm is None else f'{layer.thickness_mm * 1000:g} µm'
            extent = dc.GetTextExtent(thickness)
            dc.DrawText(thickness, max(150, width - extent.width - 8), y)


class StackupView(wx.Panel):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.stack = None
        layout = wx.BoxSizer(wx.VERTICAL)
        heading = wx.BoxSizer(wx.HORIZONTAL)
        self.title = wx.StaticText(self, label=title, style=wx.ST_ELLIPSIZE_END)
        self.title.SetMinSize((1, -1))
        self.title.SetFont(self.GetFont().Bold())
        heading.Add(self.title, 1, wx.ALIGN_CENTER_VERTICAL)
        self.details_button = wx.Button(self, label='Details…')
        self.details_button.Bind(wx.EVT_BUTTON, self._details)
        heading.Add(self.details_button)
        layout.Add(heading, 0, wx.EXPAND | wx.BOTTOM, 6)
        self.summary = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_NONE,
                                   size=(-1, 44))
        layout.Add(self.summary, 0, wx.EXPAND | wx.BOTTOM, 4)
        self.drawing = StackupDrawing(self)
        layout.Add(self.drawing, 1, wx.EXPAND)
        self.SetSizer(layout)
        self.set_stackup(None)

    def set_title(self, title, tooltip=''):
        self.title.SetLabel(title)
        self.title.SetToolTip(tooltip or title)
        self.Layout()

    def set_stackup(self, stack, reference=None, board_thickness=None):
        self.stack = stack
        self.summary.ChangeValue(physical_summary(stack, board_thickness))
        self.drawing.set_stackup(stack, reference)
        self.details_button.Enable(stack is not None)

    def _details(self, _event):
        if not self.stack:
            return
        with wx.Dialog(self, title=self.title.GetLabel() + ' — physical properties', size=(1000, 580),
                       style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER) as dialog:
            layout = wx.BoxSizer(wx.VERTICAL)
            table = wx.ListCtrl(dialog, style=wx.LC_REPORT | wx.LC_HRULES)
            for index, (name, width) in enumerate([('Layer / ply', 100), ('Type', 120), ('Material', 150),
                                                  ('Thickness (mm)', 120), ('Color', 110), ('εr', 70),
                                                  ('Loss tan', 80), ('Thickness locked', 130)]):
                table.InsertColumn(index, name, width=width)
            for row in self.stack.layers:
                index = table.InsertItem(table.GetItemCount(), row.label)
                values = [row.kind, row.material or 'Unspecified',
                          '—' if row.thickness_mm is None else f'{row.thickness_mm:g}', row.color or 'Unspecified',
                          'Unspecified' if row.epsilon_r is None else f'{row.epsilon_r:g}',
                          'Unspecified' if row.loss_tangent is None else f'{row.loss_tangent:g}',
                          'Yes' if row.locked else 'No']
                for column, value in enumerate(values, 1):
                    table.SetItem(index, column, value)
            layout.Add(table, 1, wx.EXPAND | wx.ALL, 12)
            layout.Add(dialog.CreateStdDialogButtonSizer(wx.OK), 0, wx.EXPAND | wx.ALL, 12)
            dialog.SetSizer(layout)
            dialog.ShowModal()
