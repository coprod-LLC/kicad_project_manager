"""Native wxPython IPC companion with project inspection and setup."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading
from typing import Callable

import wx

from . import NAME, __version__
from .context import ContextError, ProjectContext
from .preferences import data_directory, kicad_user_template_directory, save_template, load_standards
from .setup import read_setup, suggested_metadata
from .setup_ui import ProjectSetupPanel
from .settings_ui import StandardsFolderDialog
from .standards_ui import StandardsPanel
from .templates import Comparison, TemplateSourceError, apply_upgrade, compare, load_template, resolve_template
from .template_ui import (MATCH_COLOUR as FOUND_COLOUR, MODIFIED_COLOUR, MISSING_COLOUR,
                          TemplateReviewDialog, show_details, show_text_details)
from .yaml_fields import SetupError


def _wrap_text(control: wx.StaticText, value: str, width: int) -> str:
    # wx 3.2's Mac Wrap uses partial glyph extents that can underestimate a
    # complete line. Measure whole candidate lines with the actual label font.
    lines = []
    for paragraph in value.split('\n'):
        line = ''
        for word in paragraph.split():
            candidate = f'{line} {word}' if line else word
            if line and control.GetTextExtent(candidate).width > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return '\n'.join(lines)


class ProjectManagerFrame(wx.Frame):
    def __init__(self, provider: Callable[[], ProjectContext], storage: Path | None = None):
        super().__init__(None, title=f"{NAME} — {__version__}", size=(1100, 940))
        self._provider = provider
        self.storage = storage
        self._closing = False
        self._busy = False
        self._context: ProjectContext | None = None
        self.template_comparison: Comparison | None = None
        self.template_source: Path | None = None
        self.template_error_details: str | None = None
        self._overview_layout_pending = False
        icon_path = Path(__file__).resolve().parent.parent / "resources" / "icon.png"
        if icon_path.is_file():
            self.SetIcon(wx.Icon(str(icon_path), wx.BITMAP_TYPE_PNG))

        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(panel)
        self.refresh_button = wx.Button(panel, label="Refresh")
        self.refresh_button.SetToolTip('Refresh the active project and reload the standards catalog.')
        self.refresh_button.Bind(wx.EVT_BUTTON, lambda event: self.refresh())
        self.notebook.Bind(wx.EVT_SIZE, self._on_notebook_size)
        overview = self.overview = wx.ScrolledWindow(self.notebook, style=wx.VSCROLL)
        overview.SetScrollRate(0, 12)
        overview_layout = wx.BoxSizer(wx.VERTICAL)
        box = wx.StaticBoxSizer(wx.VERTICAL, overview, "Active project")
        grid = wx.FlexGridSizer(cols=2, vgap=12, hgap=16)
        grid.AddGrowableCol(1)
        self.fields: dict[str, wx.TextCtrl] = {}
        for key, label in [
            ("project_name", "Project"), ("project_file", "Project file"),
            ("board_file", "Board file"), ("kicad_version", "KiCad version"),
        ]:
            grid.Add(wx.StaticText(box.GetStaticBox(), label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            field = wx.TextCtrl(box.GetStaticBox(), value="—", style=wx.TE_READONLY | wx.BORDER_NONE)
            self.fields[key] = field
            grid.Add(field, 1, wx.EXPAND)
        self.template_label = wx.StaticText(box.GetStaticBox(), label='KiCad template')
        grid.Add(self.template_label, 0, wx.ALIGN_CENTER_VERTICAL)
        template_row = wx.BoxSizer(wx.HORIZONTAL)
        self.template_path = wx.TextCtrl(box.GetStaticBox(), value='No template selected',
                                        style=wx.TE_READONLY | wx.BORDER_NONE)
        self.template_path.SetMinSize((1, -1))
        self._neutral_colour = self.template_path.GetForegroundColour()
        template_row.Add(self.template_path, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.choose_template_button = wx.Button(box.GetStaticBox(), label='Choose template…')
        self.choose_template_button.Bind(wx.EVT_BUTTON, lambda event: self._choose_template())
        template_row.Add(self.choose_template_button, 0)
        grid.Add(template_row, 1, wx.EXPAND)
        grid.AddSpacer(0)
        template_box = self._template_box = wx.BoxSizer(wx.VERTICAL)
        self._template_status_text = 'Choose a template to check project files.'
        self.template_status = wx.StaticText(box.GetStaticBox(), label=self._template_status_text,
                                             style=wx.ST_NO_AUTORESIZE)
        self.template_status.SetMinSize((1, -1))
        template_box.Add(self.template_status, 0, wx.EXPAND)
        actions = wx.BoxSizer(wx.HORIZONTAL)
        self.template_details_button = wx.Button(box.GetStaticBox(), label='Details…')
        self.template_details_button.Bind(wx.EVT_BUTTON, self._template_details)
        self.template_review_button = wx.Button(box.GetStaticBox(), label='Review upgrade…')
        self.template_review_button.Bind(wx.EVT_BUTTON, self._review_template)
        for button in (self.template_details_button, self.template_review_button):
            button.Disable()
            actions.Add(button, 0, wx.RIGHT, 8)
        template_box.Add(actions, 0, wx.TOP, 6)
        grid.Add(template_box, 1, wx.EXPAND)
        grid.Add(wx.StaticText(box.GetStaticBox(), label='Standards repository'), 0, wx.ALIGN_CENTER_VERTICAL)
        standards_row = wx.BoxSizer(wx.HORIZONTAL)
        self.standards_path = wx.TextCtrl(box.GetStaticBox(), value='No standards folder selected',
                                         style=wx.TE_READONLY | wx.BORDER_NONE)
        self.standards_path.SetMinSize((1, -1))
        standards_row.Add(self.standards_path, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        change_standards = wx.Button(box.GetStaticBox(), label='Change…')
        change_standards.Bind(wx.EVT_BUTTON, lambda event: self._choose_standards())
        standards_row.Add(change_standards)
        grid.Add(standards_row, 1, wx.EXPAND)
        grid.AddSpacer(0)
        self.standards_status = wx.StaticText(box.GetStaticBox(), label='Choose a shared standards repository above.',
                                              style=wx.ST_NO_AUTORESIZE)
        self.standards_status.SetMinSize((1, -1))
        grid.Add(self.standards_status, 1, wx.EXPAND)
        grid.Add(wx.StaticText(box.GetStaticBox(), label='Backups folder'), 0, wx.ALIGN_CENTER_VERTICAL)
        backups_row = wx.BoxSizer(wx.HORIZONTAL)
        self.backup_path = (storage or data_directory()) / 'backups'
        self.backups_path = wx.TextCtrl(box.GetStaticBox(), value=str(self.backup_path),
                                       style=wx.TE_READONLY | wx.BORDER_NONE)
        self.backups_path.SetMinSize((1, -1))
        self.backups_path.SetToolTip(str(self.backup_path))
        backups_row.Add(self.backups_path, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.backups_button = wx.Button(box.GetStaticBox(), label='Open Backups')
        self.backups_button.SetToolTip('Backups are grouped by project and save time. Available after the first saved change.')
        self.backups_button.Bind(wx.EVT_BUTTON, self._open_backups)
        self.backups_button.Enable(self.backup_path.is_dir())
        backups_row.Add(self.backups_button)
        grid.Add(backups_row, 1, wx.EXPAND)
        box.Add(grid, 1, wx.EXPAND | wx.ALL, 14)
        overview_layout.Add(box, 0, wx.EXPAND | wx.ALL, 16)

        self._overview_note_text = ('Review template changes and add missing files here. '
                                    'Project Setup edits metadata and documentation status. '
                                    'Design Rules and Stackup use the shared standards repository. Apply after the PCB and schematic editors are closed.')
        note = self._overview_note = wx.StaticText(overview, label=self._overview_note_text,
                                                    style=wx.ST_NO_AUTORESIZE)
        note.SetMinSize((1, -1))
        overview_layout.Add(note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 16)
        overview_layout.Add(wx.StaticText(overview, label="Activity"), 0, wx.LEFT | wx.TOP, 16)
        self.log = wx.TextCtrl(overview, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 110))
        overview_layout.Add(self.log, 1, wx.EXPAND | wx.ALL, 16)
        overview.SetSizer(overview_layout)
        self.notebook.AddPage(overview, 'Overview', select=True)
        self.setup_panel = ProjectSetupPanel(self.notebook, self._on_applied, storage, self._verify_project)
        self.notebook.AddPage(self.setup_panel, 'Project Setup')
        self.rules_panel = StandardsPanel(self.notebook, storage,
                                         self._on_standards_applied, self._verify_standards,
                                         kind='rules', on_catalog=self._on_catalog)
        self.stackup_panel = StandardsPanel(self.notebook, storage,
                                           self._on_standards_applied, self._verify_standards,
                                           kind='stackups', on_catalog=self._on_catalog)
        self.standards_panels = (self.rules_panel, self.stackup_panel)
        self.notebook.AddPage(self.rules_panel, 'Design Rules')
        self.notebook.AddPage(self.stackup_panel, 'Stackup')
        layout.Add(self.notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 16)
        footer = wx.BoxSizer(wx.HORIZONTAL)
        footer.Add(wx.StaticText(panel, label=f"Version {__version__} · KiCad 9 / 10"), 1, wx.ALIGN_CENTER_VERTICAL)
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
        overview.Bind(wx.EVT_SIZE, self._on_overview_size)
        self.Centre()
        wx.CallAfter(self.refresh)

    def _append_log(self, message: str) -> None:
        self.log.AppendText(f"{datetime.now():%H:%M:%S}  {message}\n")
        self.backups_button.Enable(self.backup_path.is_dir())

    def _on_notebook_size(self, event):
        # Keep the native tabs and use the unused right end of their strip.
        wx.CallAfter(self._layout_notebook_header)
        event.Skip()

    def _layout_notebook_header(self):
        if self._closing or not self.notebook.GetPageCount():
            return
        height = (self.notebook.GetCurrentPage().GetScreenPosition().y
                  - self.notebook.GetScreenPosition().y)
        size = self.refresh_button.GetBestSize()
        self.refresh_button.SetSize(size)
        origin = self.notebook.GetPosition()
        self.refresh_button.SetPosition((origin.x + max(0, self.notebook.GetClientSize().width - size.width - 8),
                                         origin.y + max(0, (height - size.height) // 2)))
        self.refresh_button.Raise()

    def _on_overview_size(self, event):
        self._queue_overview_layout()
        event.Skip()

    def _queue_overview_layout(self):
        if not self._overview_layout_pending and not self._closing:
            self._overview_layout_pending = True
            wx.CallAfter(self._layout_overview_text)

    def _layout_overview_text(self):
        self._overview_layout_pending = False
        if self._closing:
            return
        # Native static boxes and scrollbars can leave less room than the column
        # sizer reports. Measure each allocated control, then recheck after the
        # wrapped height has changed the scrolling area.
        controls = (
                (self.template_status, self._template_status_text),
                (self.standards_status, self.standards_status.GetToolTipText() or self.standards_status.GetLabel()),
                (self._overview_note, self._overview_note_text))
        self.overview.Layout()
        for _ in range(3):
            widths = [max(1, control.GetClientSize().width) for control, _ in controls]
            for (control, value), width in zip(controls, widths):
                control.SetMinSize((1, -1))
                control.SetLabel(_wrap_text(control, value, width))
                control.InvalidateBestSize()
                control.SetMinSize((1, control.GetBestSize().height))
            self.overview.Layout()
            self.overview.FitInside()
            if all(control.GetClientSize().width == width for (control, _), width in zip(controls, widths)):
                break

    def _set_template_status(self, value: str, colour):
        self._template_status_text = value
        self.template_status.SetLabel(value)
        self.template_status.SetToolTip(value)
        self.template_status.SetForegroundColour(colour)
        self._queue_overview_layout()

    def _choose_standards(self):
        if any(panel._busy for panel in self.standards_panels):
            return
        with StandardsFolderDialog(self, self.storage) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                for panel in self.standards_panels:
                    panel.reload_catalog()

    def _open_backups(self, event):
        self.backups_button.Enable(self.backup_path.is_dir())
        if self.backup_path.is_dir() and not wx.LaunchDefaultApplication(str(self.backup_path)):
            wx.MessageBox(f'Could not open the folder. Copy this path into your file manager:\n{self.backup_path}',
                          'Backups', wx.OK | wx.ICON_ERROR, self)

    def _on_catalog(self, catalog, error):
        folder = load_standards(self.storage)
        self.standards_path.ChangeValue(folder or 'No standards folder selected')
        self.standards_path.SetToolTip(folder)
        status = (f'{catalog.name} · {len(catalog.rules)} rule profiles · {len(catalog.stackups)} stackups'
                  if catalog else error or 'Choose a shared standards repository above.')
        self.standards_status.SetLabel(status)
        self.standards_status.SetToolTip(status)
        self._queue_overview_layout()

    def refresh(self, discard: bool = False) -> None:
        if self._busy or self._closing or any(panel._busy for panel in self.standards_panels):
            return
        if not discard and not self._can_discard():
            return
        self._busy = True
        self.refresh_button.Disable()
        self.backups_button.Enable(self.backup_path.is_dir())
        self.setup_panel.clear('Reading project setup…')
        for panel in self.standards_panels:
            panel.set_context(None)
        self.template_comparison = None
        self.choose_template_button.Disable()
        self.template_details_button.Disable()
        self.template_review_button.Disable()
        for panel in self.standards_panels:
            panel.reload_catalog()

        def worker() -> None:
            context, error, setup_result = None, None, None
            template_result = (None, None, None)
            try:
                context = self._provider()
                template_directory = kicad_user_template_directory(major=int(context.kicad_version.split('.')[0]))
                if context.project_saved:
                    source = None
                    try:
                        root = Path(context.project_file).parent
                        source = resolve_template(root, self.storage, template_directory)
                        template_result = (source, compare(root, load_template(source)) if source else None, None)
                    except (ValueError, OSError) as exc:
                        template_result = (source, None, exc)
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
            wx.CallAfter(self._finish_refresh, context, error, setup_result, template_result)

        threading.Thread(target=worker, daemon=True, name="project-context").start()

    def _finish_refresh(self, context: ProjectContext | None, error: str | None, setup_result=None,
                        template_result=(None, None, None)) -> None:
        if self._closing:
            return
        self._busy = False
        self.refresh_button.Enable()
        self._context = context
        self.template_comparison = None
        self.template_source = None
        self.template_error_details = None
        for panel in self.standards_panels:
            panel.set_context(context if not error else None)
        if error or context is None:
            for field in self.fields.values():
                field.ChangeValue("—")
                field.SetForegroundColour(self._neutral_colour)
                field.SetToolTip("")
            self.template_path.ChangeValue('—')
            self._set_template_status('Project unavailable', self._neutral_colour)
            self.setup_panel.clear(error or 'Project unavailable. Click Refresh to reconnect.')
            self._append_log(error or "No project was returned.")
            return
        for key in ("project_name", "project_file", "board_file", "kicad_version"):
            value = getattr(context, key) or "No project file (standalone board)"
            self.fields[key].ChangeValue(value)
            self.fields[key].SetToolTip(value)
        source, comparison, template_error = template_result
        self.template_source, self.template_comparison = source, comparison
        self.choose_template_button.Enable(context.project_saved)
        self.choose_template_button.SetLabel('Change…' if source else 'Choose template…')
        self.template_path.ChangeValue(str(source) if source else 'No template selected')
        self.template_path.SetToolTip(str(source) if source else '')
        self.template_details_button.Enable(comparison is not None or template_error is not None)
        self.template_review_button.Enable(comparison is not None)
        if comparison:
            self._set_template_status(comparison.summary, {'green': FOUND_COLOUR, 'red': MISSING_COLOUR,
                                      'amber': MODIFIED_COLOUR, 'neutral': self._neutral_colour}[comparison.colour])
            self.template_review_button.SetLabel('Review adoption…' if not comparison.version else 'Review upgrade…')
        else:
            message = (template_error.summary if isinstance(template_error, TemplateSourceError)
                       else 'Could not check the template. See Details… for the reason.' if template_error
                       else 'Choose a template to check project files.')
            if template_error:
                self.template_error_details = f'Template: {source or "not selected"}\n\n{template_error}'
            self._set_template_status(message, MISSING_COLOUR if template_error else self._neutral_colour)
        self.Layout()
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

    def _choose_template(self):
        if self._busy or not self._context or not self._context.project_saved:
            return None
        if not self._can_discard():
            return None
        start = kicad_user_template_directory(major=int(self._context.kicad_version.split('.')[0]))
        while not start.exists() and start != start.parent:
            start = start.parent
        dialog = wx.DirDialog(self, "Choose the versioned KiCad template folder",
                              str(start) if start.exists() else "",
                              style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return None
        selected = dialog.GetPath()
        dialog.Destroy()
        try:
            template = load_template(Path(selected))
            compare(Path(self._context.project_file).parent, template)
            save_template(str(template.root), self.storage, template.identity)
        except (SetupError, OSError) as exc:
            wx.MessageBox(str(exc), 'KiCad template', wx.OK | wx.ICON_ERROR, self)
            return None
        self.template_source = template.root
        self.refresh(discard=True)
        return template

    def _template_details(self, event):
        if self.template_comparison:
            show_details(self, self.template_comparison)
        elif self.template_error_details:
            show_text_details(self, self.template_error_details)

    def _review_template(self, event):
        if self._busy or not self.template_comparison or not self._can_discard():
            return
        applied = False

        def apply_review(plan):
            nonlocal applied
            backup = apply_upgrade(plan, self.storage, self._verify_project)
            applied = True
            self._append_log(f'Template review applied. Backup: {backup}' if backup else 'Template files already match.')
            return backup

        try:
            comparison = compare(self.template_comparison.root, load_template(self.template_source))
            with TemplateReviewDialog(self, comparison, apply_plan=apply_review) as dialog:
                if dialog.ShowModal() == wx.ID_OK:
                    apply_review(dialog.plan)
        except (SetupError, OSError) as exc:
            wx.MessageBox(str(exc), 'Template upgrade', wx.OK | wx.ICON_ERROR, self)
        finally:
            if applied:
                self.refresh(discard=True)

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
        if any(panel._busy for panel in self.standards_panels):
            raise SetupError('Wait for the current standards operation to finish before applying another change.')
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
        if event.CanVeto() and any(panel._busy for panel in self.standards_panels):
            self._append_log('Wait for the current standards operation to finish before closing.')
            self.notebook.SetSelection(0)
            event.Veto()
            return
        if event.CanVeto() and not self._can_discard():
            event.Veto()
            return
        self._closing = True
        self.Destroy()
