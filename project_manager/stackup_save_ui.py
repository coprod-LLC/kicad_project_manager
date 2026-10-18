"""Saved-board capture form with impedance guides and catalog duplicate review."""
from __future__ import annotations

import threading
import wx

from .stackup import validate_metadata
from .stackup_save import prepare_preset, save_preset
from .stackup_ui import StackupView
from .standards import StandardsError


def _field(panel, grid, fields, key, label, *, multiline=False):
    grid.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
    control = wx.TextCtrl(panel, style=wx.TE_MULTILINE if multiline else 0,
                          size=(-1, 62) if multiline else wx.DefaultSize)
    control.SetMinSize((180, 62 if multiline else -1))
    fields[key] = control
    grid.Add(control, 1, wx.EXPAND)
    return control


def _positive(control, label):
    try:
        return float(control.GetValue().strip())
    except ValueError as exc:
        raise StandardsError(f'{label} must be a positive number.') from exc


class ImpedanceTargetDialog(wx.Dialog):
    def __init__(self, parent, layers, target=None):
        super().__init__(parent, title='Impedance guide', size=(620, 530),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.target = None
        self.fields = {}
        layout = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=9, hgap=12)
        grid.AddGrowableCol(1)
        grid.Add(wx.StaticText(self, label='Mode'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.mode = wx.Choice(self, choices=['Differential', 'Single-ended'])
        self.mode.SetSelection(0)
        grid.Add(self.mode, 1, wx.EXPAND)
        _field(self, grid, self.fields, 'impedance_ohms', 'Target (Ω)')
        for key, label in [('layer', 'Signal layer'), ('reference_layer', 'Reference layer')]:
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            self.fields[key] = wx.Choice(self, choices=layers)
            self.fields[key].SetSelection(0 if key == 'layer' else min(1, len(layers) - 1))
            grid.Add(self.fields[key], 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label='Geometry'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.fields['geometry'] = wx.ComboBox(self, choices=['non-coplanar microstrip', 'coplanar microstrip', 'stripline'])
        self.fields['geometry'].SetSelection(0)
        grid.Add(self.fields['geometry'], 1, wx.EXPAND)
        _field(self, grid, self.fields, 'track_width_mm', 'Track width (mm)')
        _field(self, grid, self.fields, 'pair_gap_mm', 'Pair gap (mm)')
        _field(self, grid, self.fields, 'notes', 'Notes (optional)', multiline=True)
        layout.Add(grid, 1, wx.EXPAND | wx.ALL, 14)
        self.error = wx.StaticText(self, label='')
        layout.Add(self.error, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        layout.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 14)
        self.SetSizer(layout)
        self.SetMinSize((560, 500))
        if target:
            self.mode.SetSelection(0 if target['mode'] == 'differential' else 1)
            for key, control in self.fields.items():
                if key in target:
                    if isinstance(control, wx.Choice):
                        control.SetStringSelection(target[key])
                    else:
                        control.SetValue(str(target[key]))
        self.mode.Bind(wx.EVT_CHOICE, self._mode_changed)
        self.Bind(wx.EVT_BUTTON, self._accept, id=wx.ID_OK)
        self._mode_changed(None)
        self.CentreOnParent()

    def _mode_changed(self, _event):
        self.fields['pair_gap_mm'].Enable(self.mode.GetSelection() == 0)

    def _accept(self, _event):
        try:
            target = {'mode': 'differential' if self.mode.GetSelection() == 0 else 'single_ended'}
            for key, label in [('impedance_ohms', 'Target impedance'), ('track_width_mm', 'Track width')]:
                target[key] = _positive(self.fields[key], label)
            if target['mode'] == 'differential':
                target['pair_gap_mm'] = _positive(self.fields['pair_gap_mm'], 'Pair gap')
            for key in ('layer', 'reference_layer'):
                target[key] = self.fields[key].GetStringSelection()
            if target['layer'] == target['reference_layer']:
                raise StandardsError('Signal and reference layers must be different.')
            target['geometry'] = self.fields['geometry'].GetValue().strip()
            notes = ' '.join(self.fields['notes'].GetValue().split())
            if notes:
                target['notes'] = notes
            validate_metadata({'impedance_targets': [target]})
            if not target['geometry']:
                raise StandardsError('Enter the impedance geometry.')
            self.target = target
            self.EndModal(wx.ID_OK)
        except (ValueError, OSError) as exc:
            self.error.SetLabel(str(exc))
            self.error.Wrap(max(1, self.GetClientSize().width - 28))
            self.Layout()


class SaveStackupDialog(wx.Dialog):
    NUMBER_FIELDS = [('nominal_thickness_mm', 'Nominal order thickness (mm)'),
                     ('finished_thickness_mm', 'Finished thickness (mm)'),
                     ('manufacturer_compression_thickness_mm', 'Manufacturer construction thickness (mm)'),
                     ('thickness_tolerance_percent', 'Thickness tolerance (%)'),
                     ('outer_copper_oz', 'Outer copper (oz)'), ('inner_copper_oz', 'Inner copper (oz)')]

    def __init__(self, parent, capture, catalog):
        super().__init__(parent, title='Save saved-board stackup as preset', size=(870, 740),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.capture, self.catalog = capture, catalog
        self.fields = {}
        self.targets = []
        self.plan = None
        self.saved_catalog = None
        self.selected_id = ''
        self._preset_id = None
        self._matches_key = None
        self._saving = False
        self._closed = False
        self._timer = None
        layout = wx.BoxSizer(wx.VERTICAL)
        intro = wx.StaticText(self, label=f'Uses the last saved stackup from {capture.board.name}. Save in KiCad first to include editor changes.')
        intro.Wrap(800)
        layout.Add(intro, 0, wx.EXPAND | wx.ALL, 14)
        self.pages = wx.Notebook(self)
        self._build_metadata()
        self._build_impedance()
        physical = StackupView(self.pages, 'Captured saved stackup')
        physical.set_stackup(capture.physical)
        self.pages.AddPage(physical, 'Physical stackup')
        self._build_checks()
        layout.Add(self.pages, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        self.status = wx.StaticText(self, label='')
        layout.Add(self.status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 14)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        self.save_button = self.FindWindow(wx.ID_OK)
        self.save_button.SetLabel('Save preset')
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 14)
        self.SetSizer(layout)
        self.SetMinSize((720, 580))
        for control in self.fields.values():
            control.Bind(wx.EVT_TEXT, self._schedule_check)
        self.recommended.Bind(wx.EVT_CHOICE, self._schedule_check)
        self.preferred.Bind(wx.EVT_CHECKBOX, self._schedule_check)
        self.Bind(wx.EVT_BUTTON, self._save, id=wx.ID_OK)
        self.Bind(wx.EVT_CLOSE, self._close)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._destroy)
        self._check()
        self.CentreOnParent()

    def _build_metadata(self):
        panel = wx.ScrolledWindow(self.pages, style=wx.VSCROLL)
        panel.SetScrollRate(0, 12)
        layout = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=9, hgap=12)
        grid.AddGrowableCol(1)
        for key, label in [('manufacturer', 'Manufacturer *'), ('name', 'Preset name *'),
                           ('manufacturer_stackup_id', 'Manufacturer construction code'), ('source_url', 'Source URL')]:
            _field(panel, grid, self.fields, key, label)
        for key, label in self.NUMBER_FIELDS:
            _field(panel, grid, self.fields, key, label)
        grid.Add(wx.StaticText(panel, label='Manufacturer’s preferred option'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.recommended = wx.Choice(panel, choices=['Unknown', 'Yes', 'No'])
        self.recommended.SetSelection(0)
        self.recommended.SetToolTip('Whether the manufacturer flags this as a preferred/common option. No means an alternative, not discouraged.')
        grid.Add(self.recommended, 1, wx.EXPAND)
        _field(panel, grid, self.fields, 'recommendation_checked', 'Preference checked (YYYY-MM-DD)')
        _field(panel, grid, self.fields, 'recommendation_evidence', 'Preference source / evidence')
        grid.Add(wx.StaticText(panel, label='Internal preference'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.preferred = wx.CheckBox(panel, label='Preferred for our designs')
        grid.Add(self.preferred, 1, wx.EXPAND)
        _field(panel, grid, self.fields, 'manufacturer_geometry_checked', 'Geometry checked (YYYY-MM-DD)')
        _field(panel, grid, self.fields, 'notes', 'Notes', multiline=True)
        _field(panel, grid, self.fields, 'assumptions', 'Assumptions (one per line)', multiline=True)
        layout.Add(grid, 0, wx.EXPAND | wx.ALL, 12)
        panel.SetSizer(layout)
        self.pages.AddPage(panel, 'Preset details')

    def _build_impedance(self):
        panel = wx.Panel(self.pages)
        layout = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(panel, label='Optional routing guidance. These values do not change netclasses or routing widths.')
        label.Wrap(740)
        layout.Add(label, 0, wx.EXPAND | wx.ALL, 12)
        self.guides = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES)
        for i, (label, width) in enumerate([('Mode', 110), ('Ω', 65), ('Signal', 80), ('Reference', 80),
                                          ('Geometry', 180), ('Width (mm)', 100), ('Gap (mm)', 100)]):
            self.guides.InsertColumn(i, label, width=width)
        layout.Add(self.guides, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in [('Add…', self._add_guide), ('Edit…', self._edit_guide), ('Remove', self._remove_guide)]:
            button = wx.Button(panel, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            row.Add(button, 0, wx.RIGHT, 8)
        self.guides.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._edit_guide)
        layout.Add(row, 0, wx.ALL, 12)
        panel.SetSizer(layout)
        self.pages.AddPage(panel, 'Impedance guides')

    def _build_checks(self):
        panel = wx.Panel(self.pages)
        layout = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(panel, label='Checks compare manufacturer/code and ordered copper/dielectric properties. Changes below are existing preset → saved board.')
        label.Wrap(740)
        layout.Add(label, 0, wx.EXPAND | wx.ALL, 12)
        self.matches = wx.ListBox(panel, style=wx.LB_SINGLE | wx.LB_HSCROLL, size=(-1, 130))
        self.matches.Bind(wx.EVT_LISTBOX, self._show_match)
        layout.Add(self.matches, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        self.differences = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 150))
        layout.Add(self.differences, 1, wx.EXPAND | wx.ALL, 12)
        self.use_existing = wx.Button(panel, label='Select existing preset')
        self.use_existing.Bind(wx.EVT_BUTTON, self._use_existing)
        layout.Add(self.use_existing, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.acknowledge = wx.CheckBox(panel, label='I reviewed the matches; save this as a distinct option')
        self.acknowledge.Bind(wx.EVT_CHECKBOX, lambda _event: self._update_save_button())
        layout.Add(self.acknowledge, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        panel.SetSizer(layout)
        self.pages.AddPage(panel, 'Catalog checks')

    def metadata(self):
        values = {key: control.GetValue().strip() for key, control in self.fields.items()}
        metadata = {key: value for key, value in values.items()
                    if value and key not in {key for key, _label in self.NUMBER_FIELDS} | {'assumptions', 'notes'}}
        for key, label in self.NUMBER_FIELDS:
            if values[key]:
                metadata[key] = _positive(self.fields[key], label)
        if values['notes']:
            metadata['notes'] = ' '.join(values['notes'].split())
        if values['assumptions']:
            metadata['assumptions'] = [line.strip() for line in values['assumptions'].splitlines() if line.strip()]
        metadata['manufacturer_recommended'] = (None, True, False)[self.recommended.GetSelection()]
        metadata['preferred'] = self.preferred.GetValue()
        if self.targets:
            metadata['impedance_targets'] = self.targets
        return metadata

    def _schedule_check(self, _event=None):
        if self._timer:
            self._timer.Stop()
        self.save_button.Disable()
        self._timer = wx.CallLater(200, self._check)

    def _message(self, text):
        self.status.SetLabel(text)
        self.status.Wrap(max(1, self.GetClientSize().width - 28))
        self.Layout()

    def _check(self):
        if self._closed or self._saving:
            return
        try:
            self.plan = prepare_preset(self.capture, self.catalog, self.metadata(), preset_id=self._preset_id)
            self._preset_id = self.plan.preset_id
        except (ValueError, OSError) as exc:
            self.plan = None
            self.matches.Clear()
            self._show_match(None)
            self.acknowledge.Enable(False)
            self._message(str(exc))
            self._update_save_button()
            return
        key = self.plan.matches
        if key != self._matches_key:
            self.acknowledge.SetValue(False)
            self._matches_key = key
        self.matches.Set([('Duplicate: ' if match.blocking else 'Similar: ') + match.name for match in key])
        self.matches.SetSelection(0 if key else wx.NOT_FOUND)
        self._show_match(None)
        self.acknowledge.Enable(bool(key) and not any(match.blocking for match in key))
        self.pages.SetPageText(3, f'Catalog checks ({len(key)})')
        self._message('This preset already exists. Open Catalog checks to select it.' if any(m.blocking for m in key) else
                      'Similar presets found. Open Catalog checks to review the differences.' if key else
                      'No duplicate or similar construction found. Ready to save.')
        self._update_save_button()

    def _update_save_button(self):
        matches = self.plan.matches if self.plan else ()
        self.save_button.Enable(bool(self.plan and not self._saving and not any(m.blocking for m in matches)
                                     and (not matches or self.acknowledge.GetValue())))

    def _show_match(self, _event):
        index = self.matches.GetSelection()
        match = self.plan.matches[index] if self.plan and 0 <= index < len(self.plan.matches) else None
        self.use_existing.Enable(match is not None)
        self.differences.ChangeValue(match.reason + '\n\n' + ('\n'.join(match.differences) or
            'Copper and dielectric properties are identical. Surface layers, finish or editor settings may differ.')
            if match else 'No matches to review.')

    def _use_existing(self, _event):
        index = self.matches.GetSelection()
        if self.plan and 0 <= index < len(self.plan.matches):
            self.selected_id = self.plan.matches[index].preset_id
            self.EndModal(wx.ID_APPLY)

    def _fill_guides(self):
        self.guides.DeleteAllItems()
        for target in self.targets:
            row = self.guides.InsertItem(self.guides.GetItemCount(), target['mode'].replace('_', '-'))
            for col, key in enumerate(('impedance_ohms', 'layer', 'reference_layer', 'geometry', 'track_width_mm', 'pair_gap_mm'), 1):
                value = target.get(key, '')
                self.guides.SetItem(row, col, f'{value:g}' if isinstance(value, (float, int)) else value)
        self._check()

    def _add_guide(self, _event):
        with ImpedanceTargetDialog(self, [row.name for row in self.capture.physical.copper]) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.targets.append(dialog.target)
                self._fill_guides()

    def _edit_guide(self, _event):
        index = self.guides.GetFirstSelected()
        if index >= 0:
            with ImpedanceTargetDialog(self, [row.name for row in self.capture.physical.copper], self.targets[index]) as dialog:
                if dialog.ShowModal() == wx.ID_OK:
                    self.targets[index] = dialog.target
                    self._fill_guides()

    def _remove_guide(self, _event):
        index = self.guides.GetFirstSelected()
        if index >= 0:
            del self.targets[index]
            self._fill_guides()

    def _save(self, _event):
        self._check()
        if not self.save_button.IsEnabled():
            return
        plan = self.plan
        acknowledge = self.acknowledge.GetValue()
        self._saving = True
        self.pages.Disable()
        self.save_button.Disable()
        self.FindWindow(wx.ID_CANCEL).Disable()
        self._message('Saving preset…')
        def worker():
            catalog, error = None, ''
            try:
                catalog = save_preset(plan, allow_similar=acknowledge)
            except (ValueError, OSError) as exc:
                error = str(exc)
            except Exception:
                error = 'Could not save this preset. Check the catalog folder and try again.'
            wx.CallAfter(self._finish_save, plan, catalog, error)
        threading.Thread(target=worker, daemon=True, name='stackup-save').start()

    def _finish_save(self, plan, catalog, error):
        if self._closed:
            return
        self._saving = False
        if error:
            self.pages.Enable()
            self.FindWindow(wx.ID_CANCEL).Enable()
            self._update_save_button()
            self._message(error)
        else:
            self.saved_catalog = catalog
            self.selected_id = plan.preset_id
            self.EndModal(wx.ID_OK)

    def _close(self, event):
        if self._saving:
            event.Veto()
        else:
            event.Skip()

    def _destroy(self, event):
        if event.GetEventObject() is self:
            self._closed = True
            if self._timer:
                self._timer.Stop()
        event.Skip()
