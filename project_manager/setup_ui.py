"""Native project setup form with optional preview and guarded application."""
from __future__ import annotations

from pathlib import Path
from typing import Callable
import wx
import wx.lib.scrolledpanel

from .setup import METADATA, STATUSES, Plan, Snapshot, apply, prepare
from .yaml_fields import SetupError


class PreviewDialog(wx.Dialog):
    def __init__(self, parent, plan: Plan, title: str = 'Review Project Changes', intro: str | None = None):
        super().__init__(parent, title=title, size=(850, 620),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        layout = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(self, label=intro or 'Apply these changes to the active project? Original files will be backed up locally.')
        label.Wrap(800)
        layout.Add(label, 0, wx.ALL, 14)
        diff = wx.TextCtrl(self, value=plan.diff(), style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        diff.SetFont(wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE)))
        layout.Add(diff, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        self.FindWindow(wx.ID_OK).SetLabel('Apply Changes')
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 14)
        self.SetSizer(layout)
        self.SetMinSize((640, 420))
        self.CentreOnParent()


class ProjectSetupPanel(wx.lib.scrolledpanel.ScrolledPanel):
    def __init__(self, parent, on_applied: Callable[[Path | None], None], storage: Path | None = None,
                 before_apply: Callable[[], None] | None = None):
        super().__init__(parent)
        self._on_applied = on_applied
        self.storage = storage
        self.before_apply = before_apply
        self.snapshot: Snapshot | None = None
        self._baseline = None
        self._author_display = ''
        self._author_value = ''
        self._loaded_values = {}
        layout = wx.BoxSizer(wx.VERTICAL)
        self.message = wx.StaticText(self, label='Connect to a saved template project to edit setup.')
        self.message.Wrap(740)
        layout.Add(self.message, 0, wx.EXPAND | wx.ALL, 14)
        grid = wx.FlexGridSizer(cols=2, vgap=9, hgap=14)
        grid.AddGrowableCol(1)
        self.fields: dict[str, wx.TextCtrl] = {}
        for key, label in [('PROJECT_NAME', 'Project name'), ('BOARD_NAME', 'Board name'),
                           ('COMPANY', 'Company'), ('DESIGNER', 'Authors\n(one per line)'),
                           ('REVIEWER', 'Reviewer (optional)'),
                           ('GIT_URL', 'Git web URL'), ('LOGO', 'Logo path')]:
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            field = wx.TextCtrl(self, style=wx.TE_MULTILINE if key == 'DESIGNER' else 0,
                                size=(-1, 62) if key == 'DESIGNER' else wx.DefaultSize)
            field.SetToolTip('Path relative to the project, for example Logos/logo.png' if key == 'LOGO' else label.replace('\n', ' '))
            if key == 'REVIEWER':
                field.SetToolTip('Optional reviewer name. Older projects need a template upgrade to display it.')
            self.fields[key] = field
            grid.Add(field, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label='Project status'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.status_choice = wx.Choice(self)
        self._fill_statuses(None)
        grid.Add(self.status_choice, 1, wx.EXPAND)
        layout.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        note = wx.StaticText(self, label='KiBot updates document text variables on the next CI run. Version tags select RELEASED and run ERC/DRC; selecting CHECKED first is not required by the template.')
        note.Wrap(740)
        layout.Add(note, 0, wx.EXPAND | wx.ALL, 14)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.AddStretchSpacer()
        self.preview_button = wx.Button(self, label='Preview Changes…')
        self.preview_button.Bind(wx.EVT_BUTTON, self._preview)
        row.Add(self.preview_button)
        self.apply_button = wx.Button(self, label='Apply')
        self.apply_button.SetToolTip('Save changes directly. Original files are backed up locally.')
        self.apply_button.Bind(wx.EVT_BUTTON, self._apply)
        row.Add(self.apply_button, 0, wx.LEFT, 8)
        layout.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        self.SetSizer(layout)
        self.SetupScrolling(scroll_x=False)
        self.clear('Reading the active project…')

    def _state(self):
        return (tuple(self.fields[key].GetValue() for key in METADATA), self.selected_status())

    @property
    def dirty(self) -> bool:
        return self.snapshot is not None and self._baseline is not None and self._state() != self._baseline

    def clear(self, message: str):
        self.snapshot = None
        self._baseline = None
        for field in self.fields.values():
            field.ChangeValue('')
            field.Disable()
        self._fill_statuses(None)
        self.status_choice.Disable()
        self.preview_button.Disable()
        self.apply_button.Disable()
        self.message.SetLabel(message)
        self.message.Wrap(max(400, self.GetClientSize().width - 28))
        self.Layout()

    def load(self, snapshot: Snapshot, values: dict[str, str]):
        self.snapshot = snapshot
        self._loaded_values = dict(values)
        self._author_value = values['DESIGNER']
        self._author_display = '\n'.join(values['DESIGNER'].split('; '))
        for key, field in self.fields.items():
            field.ChangeValue(self._author_display if key == 'DESIGNER' else values[key])
            field.Enable()
        self._fill_statuses(snapshot.status)
        self.preview_button.Enable()
        self.apply_button.Enable()
        self._update_status_enabled()
        self.message.SetLabel('Review the suggested values, then preview or apply your changes.' if values != snapshot.metadata
                              else 'Edit project information below, then preview or apply your changes.')
        self._baseline = self._state()
        self.Layout()
        self.FitInside()

    def metadata(self) -> dict[str, str]:
        result = {key: field.GetValue() if field.GetValue() == self._loaded_values.get(key)
                  else field.GetValue().strip() for key, field in self.fields.items()}
        authors = self.fields['DESIGNER'].GetValue()
        result['DESIGNER'] = self._author_value if authors == self._author_display else '; '.join(
            line.strip() for line in authors.splitlines() if line.strip())
        return result

    def _fill_statuses(self, current: str | None):
        descriptions = {
            'DRAFT': 'DRAFT — schematic PDF, netlist, and bill of materials',
            'PRELIMINARY': 'PRELIMINARY — schematic and PCB documents, without ERC/DRC checks',
            'CHECKED': 'CHECKED — schematic and PCB documents, with ERC/DRC checks',
        }
        codes = list(STATUSES)
        if current and current not in codes:
            codes.append(current)
        self.status_choice.Clear()
        for code in codes:
            label = descriptions.get(code, f'{code} — kept unless you choose DRAFT, PRELIMINARY, or CHECKED')
            self.status_choice.Append(label, code)
        self.select_status(current)

    def select_status(self, code: str | None):
        if not code:
            self.status_choice.SetSelection(wx.NOT_FOUND)
            return
        for index in range(self.status_choice.GetCount()):
            if self.status_choice.GetClientData(index) == code:
                self.status_choice.SetSelection(index)
                return

    def selected_status(self) -> str:
        index = self.status_choice.GetSelection()
        if index == wx.NOT_FOUND:
            return ''
        return self.status_choice.GetClientData(index)

    def _update_status_enabled(self):
        ready = self.snapshot is not None and self.snapshot.workflow is not None
        self.status_choice.Enable(ready)
        self.status_choice.SetToolTip('' if ready else 'Use template review on the Overview tab to add the missing workflow before changing project status.')

    def make_plan(self) -> Plan:
        if self.snapshot is None:
            raise SetupError('Refresh the project before preparing changes.')
        return prepare(self.snapshot, self.metadata(), self.selected_status())

    def apply_plan(self, plan: Plan):
        if self.before_apply:
            self.before_apply()
        backup = apply(plan, self.storage)
        self._baseline = self._state()
        self._on_applied(backup)

    def _preview(self, event):
        self._submit(preview=True)

    def _apply(self, event):
        self._submit(preview=False)

    def _submit(self, *, preview: bool):
        self.preview_button.Disable()
        self.apply_button.Disable()
        try:
            plan = self.make_plan()
            if not plan.changes:
                wx.MessageBox('Project setup is already up to date.', 'Project Setup', wx.OK | wx.ICON_INFORMATION, self)
                return
            if preview:
                with PreviewDialog(self, plan) as dialog:
                    if dialog.ShowModal() != wx.ID_OK:
                        return
            self.apply_plan(plan)
        except (SetupError, OSError) as exc:
            wx.MessageBox(str(exc), 'Project Setup', wx.OK | wx.ICON_ERROR, self)
        finally:
            self.preview_button.Enable(self.snapshot is not None)
            self.apply_button.Enable(self.snapshot is not None)
