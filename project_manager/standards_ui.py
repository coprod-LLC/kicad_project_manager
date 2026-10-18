"""Browse local presets, compare them with saved files, and apply the managed settings."""
from __future__ import annotations

from pathlib import Path
import json
import threading
import wx

from .context import ProjectContext
from .preferences import load_standards
from .setup import SetupError
from .setup_ui import PreviewDialog
from .standards import Catalog, Preset, compare, load_catalog, preset_details, _children
from .sexpr import atoms, forms
from .standards_apply import EditorsOpenError, apply_standards, plan_standards
from .stackup import (parse_stackup, read_board_stackup, appearance_override, recommendation_text,
                      matching_stackups, stackup_identity)
from .stackup_ui import StackupView
from .template_ui import MATCH_COLOUR, MODIFIED_COLOUR, MISSING_COLOUR


class StandardsPanel(wx.ScrolledWindow):
    def __init__(self, parent, storage: Path | None = None, on_applied=None, before_apply=None,
                 *, kind='rules', on_catalog=None):
        super().__init__(parent, style=wx.VSCROLL)
        self.SetScrollRate(0, 12)
        self.storage = storage
        self.on_applied = on_applied
        self.before_apply = before_apply
        self.kind = kind
        self.on_catalog = on_catalog
        self.catalog: Catalog | None = None
        self.context: ProjectContext | None = None
        self._closed = False
        self._busy = False
        self._generation = 0
        self._catalog_generation = 0
        self._snapshot_generation = 0
        self._snapshot_busy = False
        self._catalog_busy = False
        self.snapshot = None
        self._feedback_text = ''
        layout = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=10, hgap=14)
        grid.AddGrowableCol(1)
        self.choices = {}
        for kind, label in [(self.kind, 'Design profile' if self.kind == 'rules' else 'Stackup preset')]:
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            choice = wx.Choice(self, choices=['Keep current settings'])
            choice.SetSelection(0)
            choice.SetMinSize((1, -1))
            choice.Bind(wx.EVT_CHOICE, self._selection_changed)
            self.choices[kind] = choice
            picker_row = wx.BoxSizer(wx.HORIZONTAL)
            picker_row.Add(choice, 1, wx.EXPAND)
            self.preset_button = wx.Button(self, label='Preset details…' if self.kind == 'stackups' else 'Profile details…')
            self.preset_button.Bind(wx.EVT_BUTTON, self._preset_details)
            picker_row.Add(self.preset_button, 0, wx.LEFT, 8)
            grid.Add(picker_row, 1, wx.EXPAND)
        self.apply_button = wx.Button(self, label='Apply')
        self.apply_button.SetToolTip('Write the selected presets to the saved project. Original files are backed up on this computer.')
        self.apply_button.Bind(wx.EVT_BUTTON, self._apply)
        if self.kind == 'stackups':
            picker_row.Add(self.apply_button, 0, wx.LEFT, 8)
        layout.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 14)
        self.details = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                   size=(-1, 105 if self.kind == 'rules' else 44))
        layout.Add(self.details, 0, wx.EXPAND | wx.ALL, 14)
        if self.kind == 'stackups':
            self._build_stackups(layout)
        else:
            rule_pages = wx.Notebook(self)
            self.rule_values = wx.ListCtrl(rule_pages, style=wx.LC_REPORT | wx.LC_HRULES)
            for index, (label, width) in enumerate([('Board constraint', 320), ('Current saved value', 180),
                                                   ('Selected profile', 180)]):
                self.rule_values.InsertColumn(index, label, width=width)
            self.custom_rules = wx.ListCtrl(rule_pages, style=wx.LC_REPORT | wx.LC_HRULES)
            for index, (label, width) in enumerate([('Rule (evaluation order)', 280), ('Applies when', 260), ('Constraints', 260)]):
                self.custom_rules.InsertColumn(index, label, width=width)
            rule_pages.AddPage(self.rule_values, 'Board constraints')
            rule_pages.AddPage(self.custom_rules, 'Custom rules')
            layout.Add(rule_pages, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        note = wx.StaticText(self, label=('Net assignments stay in the schematic. Missing dependencies are reported below. '
                                         if self.kind == 'rules' else 'Schematic drawing · saved files only · * marks changed rows. ')
                                        + 'Close the PCB and schematic editors before Apply.')
        note.Wrap(740)
        layout.Add(note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        if self.kind == 'rules':
            row = wx.BoxSizer(wx.HORIZONTAL)
            row.AddStretchSpacer()
            self.compare_button = wx.Button(self, label='Compare with Saved Project')
            self.compare_button.Bind(wx.EVT_BUTTON, self._compare)
            row.Add(self.compare_button)
            self.preview_button = wx.Button(self, label='Review Changes…')
            self.preview_button.Bind(wx.EVT_BUTTON, self._preview)
            row.Add(self.preview_button, 0, wx.LEFT, 8)
            row.Add(self.apply_button, 0, wx.LEFT, 8)
            layout.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        if self.kind == 'stackups':
            self.feedback = wx.StaticText(self, label='')
            self.feedback.SetMinSize((1, -1))
            self.feedback.Hide()
            layout.Insert(2, self.feedback, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
            self.Bind(wx.EVT_SIZE, self._on_feedback_size)
        else:
            results = wx.Notebook(self)
            self.summary = wx.TextCtrl(results, style=wx.TE_MULTILINE | wx.TE_READONLY)
            self.diff = wx.TextCtrl(results, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
            self.diff.SetFont(wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE)))
            results.AddPage(self.summary, 'Comparison')
            results.AddPage(self.diff, 'Proposed Changes')
            results.SetMinSize((-1, 110))
            layout.Add(results, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        self.SetSizer(layout)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._destroyed)
        self._selection_changed()

    def _build_stackups(self, layout):
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.current_stackup = StackupView(self, 'Current saved board')
        self.selected_stackup = StackupView(self, 'Selected preset — preview')
        row.Add(self.current_stackup, 1, wx.EXPAND)
        self.stackup_arrow = wx.StaticText(self, label='→')
        font = self.GetFont()
        font.SetPointSize(32)
        self.stackup_arrow.SetFont(font.Bold())
        row.Add(self.stackup_arrow, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 10)
        row.Add(self.selected_stackup, 1, wx.EXPAND)
        layout.Add(row, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        appearance = self.appearance = wx.Panel(self)
        box = wx.BoxSizer(wx.HORIZONTAL)
        self.overrides = {}
        for key, label, options in [('mask', 'Mask', ['From preset', 'Black', 'Green', 'Red', 'Blue', 'White', 'Yellow', 'Purple']),
                                    ('silk', 'Silkscreen', ['From preset', 'White', 'Black', 'Yellow']),
                                    ('finish', 'Finish', ['From preset', 'ENIG'])]:
            box.Add(wx.StaticText(appearance, label=label), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
            choice = wx.Choice(appearance, choices=options)
            choice.SetMinSize((1, -1))
            choice.SetSelection(0)
            choice.Bind(wx.EVT_CHOICE, self._selection_changed)
            self.overrides[key] = choice
            box.Add(choice, 1, wx.RIGHT if key != 'finish' else 0, 10)
        appearance.SetSizer(box)
        layout.Add(appearance, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)

    def _draw_stackups(self):
        if self.kind != 'stackups':
            return
        current = self.snapshot.physical if self.snapshot else None
        thickness = self.snapshot.board_thickness_mm if self.snapshot else None
        self.current_stackup.set_stackup(current, board_thickness=thickness)
        matches = matching_stackups(current, self.catalog.stackups if self.catalog else ())
        title, tooltip = 'Current saved board', ''
        if len(matches) == 1:
            title += ' · ' + stackup_identity(matches[0].preset)
            if not matches[0].exact:
                title += ' (settings differ)'
                tooltip = title + '\nCopper and dielectric construction matches. Surface layers or stackup settings differ; see Details.'
        elif matches:
            title += ' · Multiple library matches'
            tooltip = title + '\n' + '\n'.join(stackup_identity(match.preset) for match in matches)
        elif current and self.catalog:
            title += ' · No library match'
        self.current_stackup.set_title(title, tooltip)
        preset = self.selected('stackups')
        selected = current
        if preset:
            from .standards_apply import merge_stackup_form
            native = preset.files['stackup_file']
            try:
                selected = parse_stackup(merge_stackup_form(current.native, native) if current else native)
            except ValueError as exc:
                self.selected_stackup.set_stackup(None)
                self.selected_stackup.summary.ChangeValue(str(exc))
                return
        self.selected_stackup.set_stackup(selected, reference=current if preset else None)
        self.current_stackup.set_stackup(current, reference=selected if preset else None, board_thickness=thickness)

    def _fill_rule_values(self, values=None):
        if self.kind != 'rules':
            return
        self.rule_values.DeleteAllItems()
        self.custom_rules.DeleteAllItems()
        preset = self.selected('rules')
        if not preset:
            return
        data = json.loads(preset.files['constraints_file'])['board']['design_settings']['rules']
        for key, value in sorted(data.items()):
            index = self.rule_values.InsertItem(self.rule_values.GetItemCount(), key.replace('_', ' '))
            self.rule_values.SetItem(index, 1, 'Not read yet' if values is None else str(values.get(key, 'Not set')))
            self.rule_values.SetItem(index, 2, str(value))
        for kind, node in forms(preset.files['custom_rules_file']):
            if kind != 'rule':
                continue
            index = self.custom_rules.InsertItem(self.custom_rules.GetItemCount(), atoms(node)[1])
            fields = _children(node)
            conditions = [atoms(condition)[1] for condition in fields.get('condition', [])]
            self.custom_rules.SetItem(index, 1, ' AND '.join(conditions) or 'All matching objects')
            self.custom_rules.SetItem(index, 2, '; '.join(' '.join(item.split()) for item in fields.get('constraint', [])))

    def _preset_details(self, _event):
        preset = self.selected(self.kind)
        if not preset:
            return
        with wx.Dialog(self, title='Stackup preset details' if self.kind == 'stackups' else 'Design profile details',
                       size=(900, 600), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER) as dialog:
            layout = wx.BoxSizer(wx.VERTICAL)
            text = preset_details(preset)
            if self.kind == 'rules':
                text += '\n\nCUSTOM RULES (later rules have priority)\n\n' + preset.files['custom_rules_file']
            control = wx.TextCtrl(dialog, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY)
            layout.Add(control, 1, wx.EXPAND | wx.ALL, 14)
            layout.Add(dialog.CreateStdDialogButtonSizer(wx.OK), 0, wx.EXPAND | wx.ALL, 14)
            dialog.SetSizer(layout)
            dialog.CentreOnParent()
            dialog.ShowModal()

    def _destroyed(self, event):
        if event.GetEventObject() is self:
            self._closed = True
            self._generation += 1
        event.Skip()

    def selected(self, kind: str) -> Preset | None:
        if kind not in self.choices:
            return None
        index = self.choices[kind].GetSelection() - 1
        entries = getattr(self.catalog, kind, ())
        preset = entries[index] if 0 <= index < len(entries) else None
        if kind == 'stackups' and preset and hasattr(self, 'overrides'):
            values = {key: choice.GetStringSelection() if choice.GetSelection() > 0 else ''
                      for key, choice in self.overrides.items()}
            return appearance_override(preset, **values)
        return preset

    def _controls(self):
        for kind, choice in self.choices.items():
            choice.Enable(not self._busy and bool(getattr(self.catalog, kind, ())))
        if self.kind == 'stackups':
            for choice in self.overrides.values():
                choice.Enable(not self._busy)
        ready = self.context is not None and self.context.project_saved and self.context.board_saved
        chosen = any(self.selected(kind) for kind in self.choices)
        self.preset_button.Enable(bool(chosen and not self._busy))
        enabled = bool(not self._busy and not self._snapshot_busy and ready and chosen)
        if self.kind == 'rules':
            self.compare_button.Enable(enabled)
            self.preview_button.Enable(enabled)
        self.apply_button.Enable(enabled)

    def _clear_comparison(self):
        if self.kind == 'stackups':
            self._show_result('')
            return
        self.diff.ChangeValue('')
        if self.context is None or not self.context.project_saved or not self.context.board_saved:
            message = 'Connect to a saved KiCad project to compare. You can still browse the catalog.'
        else:
            message = 'Choose presets, then compare them with the saved project.'
        self.summary.ChangeValue(message)

    def _show_result(self, message, *, state='neutral'):
        if self.kind == 'rules':
            self.summary.ChangeValue(message)
        else:
            self._feedback_text = message
            self.feedback.SetForegroundColour({'success': MATCH_COLOUR, 'warning': MODIFIED_COLOUR,
                                               'error': MISSING_COLOUR}.get(state, self.GetForegroundColour()))
            self._layout_feedback()

    def _on_feedback_size(self, event):
        wx.CallAfter(self._layout_feedback)
        event.Skip()

    def _layout_feedback(self):
        if self._closed:
            return
        self.feedback.SetToolTip(self._feedback_text)
        self.feedback.Show(bool(self._feedback_text))
        # Native scrollbar widths differ between GTK themes. Wrap to the actual
        # allocated text width, including a second pass if scrolling changes it.
        for _ in range(3):
            self.feedback.SetMinSize((1, -1))
            self.Layout()
            width = max(1, self.feedback.GetClientSize().width)
            self.feedback.SetLabel(self._feedback_text)
            self.feedback.Wrap(width)
            self.feedback.SetMinSize((1, self.feedback.GetBestSize().height))
            self.Layout()
            self.FitInside()
            if self.feedback.GetClientSize().width == width:
                break

    def set_context(self, context: ProjectContext | None):
        self._generation += 1
        self.context = context
        self._snapshot_generation += 1
        generation = self._snapshot_generation
        self.snapshot = None
        self._snapshot_busy = bool(self.kind == 'stackups' and context and context.board_saved)
        self._clear_comparison()
        self._controls()
        self._draw_stackups()
        if self.kind == 'rules' and context and context.project_saved and context.board_saved and self.selected('rules'):
            wx.CallAfter(self._compare, None)
        if self._snapshot_busy:
            self.current_stackup.summary.ChangeValue('Reading saved board stackup…')
            def worker():
                snapshot, error = None, None
                try:
                    snapshot = read_board_stackup(Path(context.board_file))
                except (ValueError, OSError) as exc:
                    error = str(exc)
                wx.CallAfter(self._finish_snapshot, generation, snapshot, error)
            threading.Thread(target=worker, daemon=True, name='standards-stackup').start()

    def _finish_snapshot(self, generation, snapshot, error):
        if self._closed or generation != self._snapshot_generation:
            return
        self._snapshot_busy = False
        self.snapshot = snapshot
        self._draw_stackups()
        self._controls()
        if error:
            self.current_stackup.summary.ChangeValue(error)

    def _selection_changed(self, event=None):
        text = preset_details(self.selected(self.kind))
        if self.kind == 'stackups' and self.selected('stackups'):
            data = self.selected('stackups').details
            text = recommendation_text(data)
            if data.get('finished_thickness_mm'):
                text += f' · Finished {data["finished_thickness_mm"]:g} mm'
                if data.get('thickness_tolerance_percent'):
                    text += f' ±{data["thickness_tolerance_percent"]:g}%'
            targets = data.get('impedance_targets', [])
            if targets:
                t = targets[0]
                text += (f'\n{t["impedance_ohms"]:g} Ω {t["mode"].replace("_", " ")} · {t.get("geometry", "")} · '
                         f'{t.get("layer", "unspecified layer")} → {t.get("reference_layer", "unspecified reference")} · '
                         f'width {t["track_width_mm"]:g} mm' + (f' / gap {t["pair_gap_mm"]:g} mm' if t.get('pair_gap_mm') else ''))
        self.details.ChangeValue(text)
        self._generation += 1
        self._clear_comparison()
        self._fill_rule_values()
        self._draw_stackups()
        self._controls()
        if self.kind == 'stackups' and (preset := self.selected('stackups')) and preset.status == 'draft':
            self._show_result('Draft preset: review its physical properties before fabrication.', state='warning')
        if self.kind == 'rules' and self.context and self.context.project_saved and self.context.board_saved and self.selected('rules'):
            wx.CallAfter(self._compare, None)

    def reload_catalog(self):
        if self._busy:
            return
        self._catalog_generation += 1
        generation = self._catalog_generation
        selected = {kind: preset.id if (preset := self.selected(kind)) else '' for kind in self.choices}
        folder = load_standards(self.storage)
        self.catalog = None
        self._catalog_busy = True
        for choice in self.choices.values():
            choice.Set(['Keep current settings'])
            choice.SetSelection(0)
        self._selection_changed()
        self.details.ChangeValue('Reading standards folder…' if folder else 'Choose a standards repository on Overview.')
        if not folder:
            self._finish_catalog(generation, None, None, selected)
            return

        def worker():
            catalog, error = None, None
            try:
                catalog = load_catalog(Path(folder))
            except (ValueError, OSError) as exc:
                error = str(exc)
            except Exception:
                error = 'Could not read this catalog. Check its format and click Refresh.'
            wx.CallAfter(self._finish_catalog, generation, catalog, error, selected)
        threading.Thread(target=worker, daemon=True, name='standards-catalog').start()

    def _finish_catalog(self, generation, catalog, error, selected):
        if self._closed or generation != self._catalog_generation:
            return
        self._catalog_busy = False
        self.catalog = catalog
        if catalog:
            for kind, choice in self.choices.items():
                entries = getattr(catalog, kind)
                choice.Set(['Keep current settings'] + [f'{p.name}'
                           + (' · recommended' if kind == 'stackups' and p.details.get('manufacturer_recommended') else '')
                           + (' · preferred' if p.details.get('preferred') else '') + f' [{p.status}]' for p in entries])
                choice.SetSelection(next((i + 1 for i, p in enumerate(entries) if p.id == selected[kind]), 0))
        self._selection_changed()
        if not catalog:
            self.details.ChangeValue('Catalog unavailable. Check the repository on Overview, then click Refresh.'
                                     if error else 'Choose a standards repository on Overview.')
        if error:
            self._show_result(error, state='error')
        if self.on_catalog:
            self.on_catalog(catalog, error)
        self.Layout()

    def _compare(self, event):
        context = self.context
        if self.kind != 'rules' or self._closed or self._busy or context is None:
            return
        rules, stackup = self.selected('rules'), self.selected('stackups')
        if rules is None and stackup is None:
            return
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._controls()
        self.summary.ChangeValue('Reading saved project files…')
        self.diff.ChangeValue('')

        def worker():
            result, error, values = None, None, None
            try:
                result = compare(Path(context.project_file), Path(context.board_file), rules, stackup, self.catalog,
                                 allow_rule_adoption=True)
                if rules:
                    project = json.loads(Path(context.project_file).read_text(encoding='utf-8-sig'))
                    values = project.get('board', {}).get('design_settings', {}).get('rules', {})
            except (ValueError, OSError) as exc:
                error = str(exc)
            except Exception:
                error = 'Could not compare these files. Save the project and try again.'
            wx.CallAfter(self._finish_comparison, generation, context, result, error, values)
        threading.Thread(target=worker, daemon=True, name='standards-compare').start()

    def _finish_comparison(self, generation, context, result, error, values=None):
        if self._closed or generation != self._generation:
            return
        self._busy = False
        self._controls()
        if context != self.context:
            self._clear_comparison()
        elif error:
            self.summary.ChangeValue(error)
        else:
            self.summary.ChangeValue(result.summary)
            self.diff.ChangeValue(result.diff)
            self._fill_rule_values(values)

    def _preview(self, event):
        self._submit(preview=True)

    def _apply(self, event):
        self._submit(preview=False)

    def _submit(self, *, preview: bool):
        context = self.context
        if self._busy or context is None:
            return
        try:
            if self.before_apply:
                self.before_apply()
        except (SetupError, OSError, ValueError) as exc:
            self._show_result(str(exc), state='error')
            if self.kind == 'rules':
                wx.MessageBox(str(exc), 'Standards', wx.OK | wx.ICON_ERROR, self)
            return
        rules, stackup = self.selected('rules'), self.selected('stackups')
        catalog = self.catalog
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._controls()
        self._show_result('Preparing stackup…' if self.kind == 'stackups' else 'Preparing saved-file changes…')
        if self.kind == 'rules':
            self.diff.ChangeValue('')

        def worker():
            plan, error = None, None
            try:
                plan = plan_standards(Path(context.project_file), Path(context.board_file), rules, stackup, catalog,
                                      allow_rule_adoption=True)
            except (ValueError, OSError) as exc:
                error = str(exc)
            except Exception:
                error = 'Could not prepare these standards. Save the project and try again.'
            wx.CallAfter(self._finish_plan, generation, context, plan, error, preview)
        threading.Thread(target=worker, daemon=True, name='standards-plan').start()

    def _finish_plan(self, generation, context, plan, error, preview):
        if self._closed or generation != self._generation:
            return
        if context != self.context:
            self._busy = False
            self._clear_comparison()
            self._controls()
            return
        if error or plan is None:
            self._busy = False
            self._controls()
            message = error or 'Could not prepare these standards.'
            self._show_result(message, state='error')
            if self.kind == 'rules':
                wx.MessageBox(message, 'Standards', wx.OK | wx.ICON_ERROR, self)
            return
        if self.kind == 'rules':
            self.summary.ChangeValue(plan.summary)
            self.diff.ChangeValue(plan.diff() or 'No differences in the compared settings.')
        if not plan.changes:
            self._busy = False
            self._controls()
            if self.kind == 'stackups':
                self.set_context(context)
                self._show_result('Selected stackup is already applied.', state='success')
            else:
                wx.MessageBox('Selected standards are already applied.', 'Standards', wx.OK | wx.ICON_INFORMATION, self)
            if self.on_applied:
                self.on_applied(None)
            return
        if preview or plan.missing_dependencies or plan.adopts_existing_rules:
            intro = ('Apply these standards to the saved project? Original files are backed up on this computer. '
                     'Close the PCB and schematic editors first if they are still open.')
            with PreviewDialog(self, plan, 'Review Standards Changes', intro) as dialog:
                accepted = dialog.ShowModal() == wx.ID_OK
            if not accepted or self._closed or generation != self._generation:
                self._busy = False
                self._controls()
                return
        if self.kind == 'stackups':
            self._show_result('Applying stackup…')
        self._start_commit(generation, context, plan)

    def _start_commit(self, generation, context, plan):
        storage = self.storage

        def worker():
            backup, error = None, None
            state = 'error'
            try:
                backup = apply_standards(plan, storage)
            except EditorsOpenError as exc:
                error, state = str(exc), 'warning'
            except (ValueError, OSError) as exc:
                error = str(exc)
            except Exception:
                error = 'Could not apply these standards. The original files were kept when that was still possible.'
            wx.CallAfter(self._finish_commit, generation, context, plan, backup, error, state)
        threading.Thread(target=worker, daemon=True, name='standards-apply').start()

    def _finish_commit(self, generation, context, plan, backup, error, state='error'):
        if self._closed or generation != self._generation:
            return
        self._busy = False
        self._controls()
        if context != self.context:
            self._clear_comparison()
            return
        if error:
            self._show_result(error, state=state)
            if self.kind == 'rules':
                wx.MessageBox(error, 'Standards', wx.OK | wx.ICON_ERROR, self)
            return
        if self.kind == 'stackups':
            self.set_context(self.context)
            applied = 'Stackup applied. Backup available in Overview.' if backup else 'Selected stackup is already applied.'
        elif backup:
            applied = f'Applied. Backup: {backup}\nThe notes below were taken before the files were written.\n\n{plan.summary}'
        else:
            applied = 'Selected standards are already applied.\n\n' + plan.summary
        self._show_result(applied, state='success')
        if self.on_applied:
            self.on_applied(backup)
