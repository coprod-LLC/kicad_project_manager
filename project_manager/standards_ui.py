"""Browse local presets, compare them with saved files, and apply the managed settings."""
from __future__ import annotations

from pathlib import Path
import threading
import wx

from .context import ProjectContext
from .preferences import load_standards
from .setup import SetupError
from .setup_ui import PreviewDialog
from .standards import Catalog, Preset, compare, load_catalog, preset_details
from .standards_apply import apply_standards, plan_standards


class StandardsPanel(wx.Panel):
    def __init__(self, parent, open_settings, storage: Path | None = None, on_applied=None, before_apply=None):
        super().__init__(parent)
        self.storage = storage
        self.on_applied = on_applied
        self.before_apply = before_apply
        self.catalog: Catalog | None = None
        self.context: ProjectContext | None = None
        self._closed = False
        self._busy = False
        self._generation = 0
        layout = wx.BoxSizer(wx.VERTICAL)
        self.source = wx.StaticText(self, label='Choose a standards folder in Settings.')
        self.source.Wrap(740)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self.source, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.settings_button = wx.Button(self, label='Settings…')
        self.settings_button.Bind(wx.EVT_BUTTON, lambda event: open_settings())
        row.Add(self.settings_button)
        self.reload_button = wx.Button(self, label='Reload Catalog')
        self.reload_button.Bind(wx.EVT_BUTTON, lambda event: self.reload_catalog())
        row.Add(self.reload_button, 0, wx.LEFT, 8)
        layout.Add(row, 0, wx.EXPAND | wx.ALL, 14)
        grid = wx.FlexGridSizer(cols=2, vgap=10, hgap=14)
        grid.AddGrowableCol(1)
        self.choices = {}
        for kind, label in [('rules', 'Rules'), ('stackups', 'Stackup')]:
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            choice = wx.Choice(self, choices=['Keep current settings'])
            choice.SetSelection(0)
            choice.Bind(wx.EVT_CHOICE, self._selection_changed)
            self.choices[kind] = choice
            grid.Add(choice, 1, wx.EXPAND)
        layout.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        self.details = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 155))
        layout.Add(self.details, 0, wx.EXPAND | wx.ALL, 14)
        note = wx.StaticText(self, label='Save in KiCad first. Apply writes one marked company-rules block and the selected constraints, net classes, and stackup. Rules outside that block are kept. Close the PCB and schematic editors before applying, then reopen the project.')
        note.Wrap(740)
        layout.Add(note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.AddStretchSpacer()
        self.compare_button = wx.Button(self, label='Compare with Saved Project')
        self.compare_button.Bind(wx.EVT_BUTTON, self._compare)
        row.Add(self.compare_button)
        self.preview_button = wx.Button(self, label='Preview Changes…')
        self.preview_button.Bind(wx.EVT_BUTTON, self._preview)
        row.Add(self.preview_button, 0, wx.LEFT, 8)
        self.apply_button = wx.Button(self, label='Apply')
        self.apply_button.SetToolTip('Write the selected presets to the saved project. Original files are backed up on this computer.')
        self.apply_button.Bind(wx.EVT_BUTTON, self._apply)
        row.Add(self.apply_button, 0, wx.LEFT, 8)
        layout.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        results = wx.Notebook(self)
        self.summary = wx.TextCtrl(results, style=wx.TE_MULTILINE | wx.TE_READONLY)
        self.diff = wx.TextCtrl(results, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.diff.SetFont(wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE)))
        results.AddPage(self.summary, 'Comparison')
        results.AddPage(self.diff, 'Proposed Changes')
        layout.Add(results, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        self.SetSizer(layout)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._destroyed)
        self._selection_changed()

    def _destroyed(self, event):
        if event.GetEventObject() is self:
            self._closed = True
            self._generation += 1
        event.Skip()

    def selected(self, kind: str) -> Preset | None:
        index = self.choices[kind].GetSelection() - 1
        entries = getattr(self.catalog, kind, ())
        return entries[index] if 0 <= index < len(entries) else None

    def _controls(self):
        for kind, choice in self.choices.items():
            choice.Enable(not self._busy and bool(getattr(self.catalog, kind, ())))
        self.reload_button.Enable(not self._busy)
        ready = self.context is not None and self.context.project_saved and self.context.board_saved
        chosen = any(self.selected(kind) for kind in self.choices)
        enabled = bool(not self._busy and ready and chosen)
        self.compare_button.Enable(enabled)
        self.preview_button.Enable(enabled)
        self.apply_button.Enable(enabled)

    def _clear_comparison(self):
        self.diff.ChangeValue('')
        if self.context is None or not self.context.project_saved or not self.context.board_saved:
            message = 'Connect to a saved KiCad project to compare. You can still browse the catalog.'
        else:
            message = 'Choose presets, then compare them with the saved project.'
        self.summary.ChangeValue(message)

    def set_context(self, context: ProjectContext | None):
        self.context = context
        self._clear_comparison()
        self._controls()

    def _selection_changed(self, event=None):
        self.details.ChangeValue('\n\n'.join(f'{label}\n{preset_details(self.selected(kind))}'
                                             for kind, label in [('rules', 'Rules'), ('stackups', 'Stackup')]))
        self._clear_comparison()
        self._controls()

    def reload_catalog(self):
        self._generation += 1
        generation = self._generation
        selected = {kind: preset.id if (preset := self.selected(kind)) else '' for kind in self.choices}
        folder = load_standards(self.storage)
        self.catalog = None
        self._busy = True
        for choice in self.choices.values():
            choice.Set(['Keep current settings'])
            choice.SetSelection(0)
        self._selection_changed()
        self.source.SetLabel('Reading standards folder…' if folder else 'Choose a standards folder in Settings.')
        self.source.SetToolTip(folder)
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
                error = 'Could not read this catalog. Check its format and choose Reload Catalog.'
            wx.CallAfter(self._finish_catalog, generation, catalog, error, selected)
        threading.Thread(target=worker, daemon=True, name='standards-catalog').start()

    def _finish_catalog(self, generation, catalog, error, selected):
        if self._closed or generation != self._generation:
            return
        self._busy = False
        self.catalog = catalog
        if catalog:
            self.source.SetLabel(catalog.name)
            for kind, choice in self.choices.items():
                entries = getattr(catalog, kind)
                choice.Set(['Keep current settings'] + [f'{p.name} [{p.status}]' for p in entries])
                choice.SetSelection(next((i + 1 for i, p in enumerate(entries) if p.id == selected[kind]), 0))
        elif error:
            self.source.SetLabel('Catalog unavailable — open Settings or reload after fixing the folder.')
        self.source.Wrap(max(300, self.GetClientSize().width - 290))
        self._selection_changed()
        if error:
            self.summary.ChangeValue(error)
        self.Layout()

    def _compare(self, event):
        context = self.context
        if self._busy or context is None:
            return
        rules, stackup = self.selected('rules'), self.selected('stackups')
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._controls()
        self.summary.ChangeValue('Reading saved project files…')
        self.diff.ChangeValue('')

        def worker():
            result, error = None, None
            try:
                result = compare(Path(context.project_file), Path(context.board_file), rules, stackup, self.catalog)
            except (ValueError, OSError) as exc:
                error = str(exc)
            except Exception:
                error = 'Could not compare these files. Save the project and try again.'
            wx.CallAfter(self._finish_comparison, generation, context, result, error)
        threading.Thread(target=worker, daemon=True, name='standards-compare').start()

    def _finish_comparison(self, generation, context, result, error):
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
            wx.MessageBox(str(exc), 'Standards', wx.OK | wx.ICON_ERROR, self)
            return
        rules, stackup = self.selected('rules'), self.selected('stackups')
        catalog = self.catalog
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._controls()
        self.summary.ChangeValue('Preparing saved-file changes…')
        self.diff.ChangeValue('')

        def worker():
            plan, error = None, None
            try:
                plan = plan_standards(Path(context.project_file), Path(context.board_file), rules, stackup, catalog)
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
            self.summary.ChangeValue(error or 'Could not prepare these standards.')
            wx.MessageBox(self.summary.GetValue(), 'Standards', wx.OK | wx.ICON_ERROR, self)
            return
        self.summary.ChangeValue(plan.summary)
        self.diff.ChangeValue(plan.diff() or 'No differences in the compared settings.')
        if not plan.changes:
            self._busy = False
            self._controls()
            wx.MessageBox('Selected standards are already applied.', 'Standards', wx.OK | wx.ICON_INFORMATION, self)
            if self.on_applied:
                self.on_applied(None)
            return
        if preview:
            intro = ('Apply these standards to the saved project? Original files are backed up on this computer. '
                     'Close the PCB and schematic editors first if they are still open.')
            with PreviewDialog(self, plan, 'Review Standards Changes', intro) as dialog:
                accepted = dialog.ShowModal() == wx.ID_OK
            if not accepted or self._closed or generation != self._generation:
                self._busy = False
                self._controls()
                return
        self._start_commit(generation, context, plan)

    def _start_commit(self, generation, context, plan):
        storage = self.storage

        def worker():
            backup, error = None, None
            try:
                backup = apply_standards(plan, storage)
            except (ValueError, OSError) as exc:
                error = str(exc)
            except Exception:
                error = 'Could not apply these standards. The original files were kept when that was still possible.'
            wx.CallAfter(self._finish_commit, generation, context, plan, backup, error)
        threading.Thread(target=worker, daemon=True, name='standards-apply').start()

    def _finish_commit(self, generation, context, plan, backup, error):
        if self._closed or generation != self._generation:
            return
        self._busy = False
        self._controls()
        if context != self.context:
            self._clear_comparison()
            return
        if error:
            self.summary.ChangeValue(error)
            wx.MessageBox(error, 'Standards', wx.OK | wx.ICON_ERROR, self)
            return
        if backup:
            applied = f'Applied. Backup: {backup}\nThe notes below were taken before the files were written.\n\n{plan.summary}'
        else:
            applied = 'Selected standards are already applied.\n\n' + plan.summary
        self.summary.ChangeValue(applied)
        if self.on_applied:
            self.on_applied(backup)
