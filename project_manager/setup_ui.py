"""Native project setup form and explicit preview/apply interaction."""
from __future__ import annotations

from pathlib import Path
from typing import Callable
import wx
import wx.lib.scrolledpanel

from .preferences import load_template, save_template
from .setup import METADATA, STATUSES, Plan, Snapshot, apply, prepare
from .yaml_fields import SetupError


class PreviewDialog(wx.Dialog):
    def __init__(self, parent, plan: Plan):
        super().__init__(parent, title='Review Project Changes', size=(850, 620),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        layout = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(self, label='Apply these changes to the active project? Original files will be backed up locally.')
        label.Wrap(800)
        layout.Add(label, 0, wx.ALL, 14)
        diff = wx.TextCtrl(self, value=plan.diff(), style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        diff.SetFont(wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE)))
        layout.Add(diff, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        self.FindWindowById(wx.ID_OK).SetLabel('Apply Changes')
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
                           ('GIT_URL', 'Git web URL'), ('LOGO', 'Logo path')]:
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            field = wx.TextCtrl(self, style=wx.TE_MULTILINE if key == 'DESIGNER' else 0,
                                size=(-1, 62) if key == 'DESIGNER' else wx.DefaultSize)
            field.SetToolTip('Path relative to the project, for example Logos/logo.png' if key == 'LOGO' else label.replace('\n', ' '))
            self.fields[key] = field
            grid.Add(field, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label='Project status'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.status_choice = wx.Choice(self, choices=list(STATUSES))
        grid.Add(self.status_choice, 1, wx.EXPAND)
        layout.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        self.status_help = wx.StaticText(self, label='')
        layout.Add(self.status_help, 0, wx.EXPAND | wx.ALL, 14)
        self.status_choice.Bind(wx.EVT_CHOICE, lambda event: self.update_status_help())
        box = wx.StaticBoxSizer(wx.VERTICAL, self, 'GitHub Workflow')
        self.restore = wx.CheckBox(box.GetStaticBox(), label='Restore missing .github/workflows/ci.yaml from a local template')
        self.restore.Bind(wx.EVT_CHECKBOX, lambda event: self._toggle_workflow())
        box.Add(self.restore, 0, wx.ALL, 10)
        self.template = wx.DirPickerCtrl(box.GetStaticBox(), path=load_template(storage),
                                        message='Choose your Coprod or KDT template checkout',
                                        style=wx.DIRP_DEFAULT_STYLE | wx.DIRP_USE_TEXTCTRL)
        self.template.SetToolTip('Choose the template root containing kibot_yaml and .github.')
        self.template.Bind(wx.EVT_DIRPICKER_CHANGED, self._remember_template)
        box.Add(self.template, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.workflow_help = wx.StaticText(box.GetStaticBox(), label='The selected template folder is remembered on this computer.')
        self.workflow_help.Wrap(700)
        box.Add(self.workflow_help, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        layout.Add(box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        note = wx.StaticText(self, label='KiBot updates document text variables on the next CI run. RELEASED is selected by version tags.')
        note.Wrap(740)
        layout.Add(note, 0, wx.EXPAND | wx.ALL, 14)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.AddStretchSpacer()
        self.preview_button = wx.Button(self, label='Preview Changes…')
        self.preview_button.Bind(wx.EVT_BUTTON, self._preview)
        row.Add(self.preview_button)
        layout.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        self.SetSizer(layout)
        self.SetupScrolling(scroll_x=False)
        self.clear('Reading the active project…')

    def _state(self):
        return (tuple(self.fields[key].GetValue() for key in METADATA),
                self.status_choice.GetStringSelection(), self.restore.GetValue(), self.template.GetPath())

    @property
    def dirty(self) -> bool:
        return self.snapshot is not None and self._baseline is not None and self._state() != self._baseline

    def clear(self, message: str):
        self.snapshot = None
        self._baseline = None
        for field in self.fields.values():
            field.ChangeValue('')
            field.Disable()
        self.status_choice.SetSelection(wx.NOT_FOUND)
        self.status_choice.Disable()
        self.restore.SetValue(False)
        self.restore.Disable()
        self.template.Disable()
        self.preview_button.Disable()
        self.message.SetLabel(message)
        self.message.Wrap(max(400, self.GetClientSize().width - 28))
        self.status_help.SetLabel('')
        self.Layout()

    def load(self, snapshot: Snapshot, values: dict[str, str]):
        self.snapshot = snapshot
        self._loaded_values = dict(values)
        self._author_value = values['DESIGNER']
        self._author_display = '\n'.join(values['DESIGNER'].split('; '))
        for key, field in self.fields.items():
            field.ChangeValue(self._author_display if key == 'DESIGNER' else values[key])
            field.Enable()
        choices = list(STATUSES)
        if snapshot.status not in choices:
            choices.append(snapshot.status)
        self.status_choice.Set(choices)
        self.status_choice.SetStringSelection(snapshot.status)
        missing = snapshot.workflow is None
        self.restore.Enable(missing)
        self.restore.SetValue(missing)
        self.workflow_help.SetLabel('Select the template root; its branch and release policies will be preserved.' if missing
                                    else 'Workflow found. Its existing jobs and branch policies will be preserved.')
        self.workflow_help.Wrap(700)
        self.preview_button.Enable()
        self._toggle_workflow()
        self.message.SetLabel('Review the suggested values, then preview your changes.' if values != snapshot.metadata
                              else 'Edit project information below, then preview your changes.')
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

    def _toggle_workflow(self):
        missing = self.snapshot is not None and self.snapshot.workflow is None
        self.template.Enable(missing and self.restore.GetValue())
        self.status_choice.Enable(self.snapshot is not None and (not missing or self.restore.GetValue()))
        self.update_status_help()

    def update_status_help(self):
        descriptions = {'DRAFT': 'DRAFT — schematic PDF, netlist, and bill of materials.',
                        'PRELIMINARY': 'PRELIMINARY — schematic and PCB documents, without ERC/DRC checks.',
                        'CHECKED': 'CHECKED — schematic and PCB documents, with ERC/DRC checks.'}
        text = descriptions.get(self.status_choice.GetStringSelection(), 'Existing custom status is preserved unless you select another.')
        if self.snapshot and self.snapshot.workflow is None and not self.restore.GetValue():
            text = 'Restore the workflow to enable project status changes.'
        self.status_help.SetLabel(text)
        self.status_help.Wrap(720)
        self.Layout()

    def _remember_template(self, event):
        try:
            if Path(self.template.GetPath()).is_dir():
                save_template(self.template.GetPath(), self.storage)
        except OSError as exc:
            wx.MessageBox(f'Could not remember the template folder: {exc}', 'Project Setup', wx.OK | wx.ICON_ERROR, self)

    def make_plan(self) -> Plan:
        if self.snapshot is None:
            raise SetupError('Refresh the project before preparing changes.')
        template = Path(self.template.GetPath()) if self.template.GetPath() else None
        return prepare(self.snapshot, self.metadata(), self.status_choice.GetStringSelection(),
                       template, self.restore.GetValue())

    def apply_plan(self, plan: Plan):
        if self.before_apply:
            self.before_apply()
        backup = apply(plan, self.storage)
        self._baseline = self._state()
        self._on_applied(backup)

    def _preview(self, event):
        try:
            plan = self.make_plan()
            if not plan.changes:
                wx.MessageBox('Project setup is already up to date.', 'Project Setup', wx.OK | wx.ICON_INFORMATION, self)
                return
            with PreviewDialog(self, plan) as dialog:
                if dialog.ShowModal() != wx.ID_OK:
                    return
            self.preview_button.Disable()
            self.apply_plan(plan)
        except (SetupError, OSError) as exc:
            wx.MessageBox(str(exc), 'Project Setup', wx.OK | wx.ICON_ERROR, self)
        finally:
            self.preview_button.Enable(self.snapshot is not None)
