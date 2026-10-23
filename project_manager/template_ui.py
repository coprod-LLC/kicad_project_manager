"""Native template comparison and per-file migration review."""
from __future__ import annotations

from pathlib import Path
from typing import Callable
import wx

from .setup_ui import PreviewDialog
from .diff_ui import DIFF_ADDED_COLOUR, DIFF_REMOVED_COLOUR, show_diff
from .impedance_table import ImpedanceTable
from .impedance_ui import ImpedancePanel
from .templates import (README_SOURCE, Comparison, Item, Upgrade, apply_upgrade, compare,
                        load_template, prepare_upgrade, split_ignore_rules, text)
from .yaml_fields import SetupError

MATCH_COLOUR = wx.Colour(46, 170, 80)
CUSTOM_COLOUR = wx.Colour(65, 135, 210)
MODIFIED_COLOUR = wx.Colour(220, 160, 50)
MISSING_COLOUR = wx.Colour(220, 70, 70)


def item_colour(item: Item) -> wx.Colour:
    if item.current is None:
        return MISSING_COLOUR
    if item.accepted:
        return CUSTOM_COLOUR
    return MATCH_COLOUR if item.status == 'Matches' else MODIFIED_COLOUR


class TemplateReviewDialog(wx.Dialog):
    """One review workspace, with editable documents separated from shared files."""
    def __init__(self, parent, comparison: Comparison,
                 apply_plan: Callable[[Upgrade], Path | None] | None = None,
                 standards_directory: Path | None = None, board_file: Path | None = None):
        super().__init__(parent, title='Review Template Upgrade', size=(1200, 800),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.comparison = comparison
        self.choices = {item.path: 'keep' if item.status == 'Matches' or item.accepted else 'review'
                        for item in comparison.items}
        self.edits = {item.path: item.merged for item in comparison.items if item.merged is not None}
        self._edited_paths: set[str] = set()
        self.selected = -1
        self.visible_indices: list[int] = []
        self.category = 'documents' if any(item.editable for item in comparison.items) else 'template'
        self._document_view = 0
        self._selection_pending = False
        self._loading = False
        self._table_path: str | None = None
        self._table_available = False
        self._apply_plan = apply_plan or apply_upgrade
        self.applied = False
        self.refreshed = False
        layout = wx.BoxSizer(wx.VERTICAL)
        self.intro = wx.StaticText(self, label=self._intro())
        layout.Add(self.intro, 0, wx.EXPAND | wx.ALL, 12)
        categories = wx.BoxSizer(wx.HORIZONTAL)
        self.documents_button = wx.ToggleButton(self, label='Project documents')
        self.template_button = wx.ToggleButton(self, label='Template files')
        for button, category in ((self.documents_button, 'documents'), (self.template_button, 'template')):
            button.Bind(wx.EVT_TOGGLEBUTTON, lambda event, value=category: self.show_category(value))
            categories.Add(button, 0, wx.RIGHT, 8)
        categories.AddStretchSpacer()
        self.pending_label = wx.StaticText(self)
        categories.Add(self.pending_label, 0, wx.ALIGN_CENTER_VERTICAL)
        self.refresh_button = wx.Button(self, label='Refresh')
        self.refresh_button.SetToolTip('Reload project and installed template files; retain compatible drafts and choices.')
        self.refresh_button.Bind(wx.EVT_BUTTON, self._refresh)
        categories.Add(self.refresh_button, 0, wx.LEFT, 12)
        layout.Add(categories, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE | wx.SP_3D)
        self.splitter.SetMinimumPaneSize(250)
        self.splitter.SetSashGravity(0)
        left = wx.Panel(self.splitter)
        left_layout = wx.BoxSizer(wx.VERTICAL)
        self.search = wx.SearchCtrl(left, style=wx.TE_PROCESS_ENTER)
        self.search.SetDescriptiveText('Find a file…')
        self.search.ShowCancelButton(True)
        self.search.Bind(wx.EVT_TEXT, lambda event: self._populate())
        self.search.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, lambda event: self.search.SetValue(''))
        left_layout.Add(self.search, 0, wx.EXPAND | wx.BOTTOM, 8)
        self.files = wx.ListCtrl(left, style=wx.LC_REPORT)
        for index, (label, width) in enumerate((('File', 158), ('State', 94), ('Action', 106))):
            self.files.InsertColumn(index, label, width=width)
        self.files.Bind(wx.EVT_LIST_ITEM_SELECTED, self._select)
        self.files.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._select)
        self.files.Bind(wx.EVT_SIZE, self._size_columns)
        left_layout.Add(self.files, 1, wx.EXPAND)
        hint = wx.StaticText(left, label='Shift / Ctrl (⌘ on Mac): select several files')
        left_layout.Add(hint, 0, wx.TOP, 8)
        left.SetSizer(left_layout)

        right = wx.Panel(self.splitter)
        self._right = right
        self._context_note = ''
        right.Bind(wx.EVT_SIZE, self._resize_context)
        right_layout = wx.BoxSizer(wx.VERTICAL)
        self.selection_label = wx.StaticText(right, label='Select a file')
        self.selection_label.SetFont(self.selection_label.GetFont().Bold())
        right_layout.Add(self.selection_label, 0, wx.EXPAND | wx.BOTTOM, 4)
        self.path_label = wx.StaticText(right, style=wx.ST_ELLIPSIZE_MIDDLE)
        right_layout.Add(self.path_label, 0, wx.EXPAND | wx.BOTTOM, 8)
        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self.edit_button = wx.ToggleButton(right, label='Edit')
        self.source_button = wx.ToggleButton(right, label='Text')
        self.changes_button = wx.ToggleButton(right, label='Changes')
        for button, view in ((self.edit_button, 0), (self.source_button, 2), (self.changes_button, 1)):
            button.Bind(wx.EVT_TOGGLEBUTTON, lambda event, value=view: self._show_view(value))
            toolbar.Add(button, 0, wx.RIGHT, 6)
        toolbar.AddStretchSpacer()
        toolbar.Add(wx.StaticText(right, label='On Apply:'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.action = wx.Choice(right)
        self.action.SetMinSize((155, -1))
        self.action.Bind(wx.EVT_CHOICE, self._action_changed)
        toolbar.Add(self.action, 0)
        right_layout.Add(toolbar, 0, wx.EXPAND | wx.BOTTOM, 8)
        self.context_label = wx.StaticText(right)
        right_layout.Add(self.context_label, 0, wx.EXPAND | wx.BOTTOM, 8)
        # Give hidden book pages a real initial allocation. GTK otherwise tries
        # to allocate their scrolled text controls at 1×1 during page changes.
        self.views = wx.Simplebook(right, size=(600, 420))
        editor_panel = wx.Panel(self.views, size=(600, 420))
        editor_layout = wx.BoxSizer(wx.VERTICAL)
        self.shared_toggle = wx.CheckBox(editor_panel, label='Show shared ignore rules (read only)')
        self.shared_toggle.Bind(wx.EVT_CHECKBOX, self._toggle_shared_rules)
        editor_layout.Add(self.shared_toggle, 0, wx.BOTTOM, 8)
        self.ignore_splitter = wx.SplitterWindow(editor_panel, style=wx.SP_LIVE_UPDATE | wx.SP_3D)
        self.ignore_splitter.SetMinimumPaneSize(90)
        self.ignore_splitter.SetSashGravity(0.5)
        self.shared_panel = wx.Panel(self.ignore_splitter)
        shared_layout = wx.BoxSizer(wx.VERTICAL)
        shared_layout.Add(wx.StaticText(self.shared_panel, label='Shared template rules (read only)'), 0, wx.BOTTOM, 5)
        self.shared_rules = wx.TextCtrl(self.shared_panel, size=(-1, 150),
                                        style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        shared_layout.Add(self.shared_rules, 1, wx.EXPAND)
        self.shared_panel.SetSizer(shared_layout)
        user_panel = wx.Panel(self.ignore_splitter)
        user_layout = wx.BoxSizer(wx.VERTICAL)
        self.user_rules_label = wx.StaticText(user_panel, label='Project rules (editable)')
        user_layout.Add(self.user_rules_label, 0, wx.BOTTOM, 5)
        self.user_rules_label.Hide()
        self.editor = wx.TextCtrl(user_panel, style=wx.TE_MULTILINE | wx.HSCROLL | wx.TE_DONTWRAP)
        self.editor.Bind(wx.EVT_TEXT, self._edited)
        user_layout.Add(self.editor, 1, wx.EXPAND)
        user_panel.SetSizer(user_layout)
        self.ignore_splitter.Initialize(user_panel)
        self.shared_panel.Hide()
        self._user_panel = user_panel
        editor_layout.Add(self.ignore_splitter, 1, wx.EXPAND)
        editor_panel.SetSizer(editor_layout)
        diff_panel = wx.Panel(self.views)
        diff_layout = wx.BoxSizer(wx.VERTICAL)
        self.diff_mode = wx.Choice(diff_panel, choices=['Compare with template', 'Changes to apply'])
        self.diff_mode.SetSelection(0)
        self.diff_mode.Bind(wx.EVT_CHOICE, self._diff_mode_changed)
        diff_layout.Add(self.diff_mode, 0, wx.BOTTOM, 8)
        self.diff = wx.TextCtrl(diff_panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL | wx.TE_RICH2)
        diff_layout.Add(self.diff, 1, wx.EXPAND)
        diff_panel.SetSizer(diff_layout)
        for control in (self.diff, self.editor, self.shared_rules):
            control.SetFont(wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE)))
        self.views.AddPage(editor_panel, 'Edit')
        self.views.AddPage(diff_panel, 'Changes')
        self.impedance_panel = ImpedancePanel(self.views, self._table_changed, standards_directory, board_file)
        self.views.AddPage(self.impedance_panel, 'Impedance table')
        right_layout.Add(self.views, 1, wx.EXPAND)
        padded_right = wx.BoxSizer(wx.VERTICAL)
        padded_right.Add(right_layout, 1, wx.EXPAND | wx.LEFT, 12)
        right.SetSizer(padded_right)
        self.splitter.SplitVertically(left, right, 380)
        layout.Add(self.splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        legend = wx.StaticText(self, label='Green: matches  ·  Blue: accepted customization  ·  '
                                         'Amber: review needed  ·  Red: missing')
        layout.Add(legend, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.feedback = wx.StaticText(self)
        layout.Add(self.feedback, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        note = wx.StaticText(self, label='Apply saves all chosen actions in both lists. Originals are backed up.')
        buttons.Add(note, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        buttons.Add(wx.Button(self, wx.ID_CANCEL, 'Close'))
        self.Bind(wx.EVT_BUTTON, self._close, id=wx.ID_CANCEL)
        self.Bind(wx.EVT_CLOSE, self._close)
        self.preview_button = wx.Button(self, wx.ID_OK, 'Preview Apply…')
        self.Bind(wx.EVT_BUTTON, self._preview, id=wx.ID_OK)
        buttons.Add(self.preview_button, 0, wx.LEFT, 8)
        self.apply_button = wx.Button(self, wx.ID_APPLY, 'Apply')
        self.apply_button.SetToolTip('Save the chosen actions and continue reviewing.')
        self.apply_button.Bind(wx.EVT_BUTTON, self._apply)
        buttons.Add(self.apply_button, 0, wx.LEFT, 8)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetMinSize((1000, 650))
        self.CentreOnParent()
        self._populate()

    def _intro(self):
        return self.comparison.summary

    @staticmethod
    def _action_label(action):
        return {'review': 'Leave pending', 'keep': 'Keep project',
                'use': 'Use template', 'edit': 'Save edits'}[action]

    @staticmethod
    def _state_label(item):
        return ('Missing' if item.current is None else 'Customized' if item.accepted else
                'Matches' if item.status == 'Matches' else 'Review')

    def show_category(self, category):
        self.category = category
        self.search.ChangeValue('')
        self._populate(selected_paths=[])

    def _size_columns(self, event):
        width = self.files.GetClientSize().width
        self.files.SetColumnWidth(0, max(125, width - 205))
        event.Skip()

    def _selected_indices(self):
        """Comparison indices, independent of the current filter and row order."""
        indices, row = [], self.files.GetFirstSelected()
        while row != -1:
            indices.append(self.visible_indices[row])
            row = self.files.GetNextSelected(row)
        return indices

    def select_path(self, path):
        """Reveal one file without discarding choices or document drafts."""
        index = next(i for i, item in enumerate(self.comparison.items) if item.path == path)
        self.category = 'documents' if self.comparison.items[index].editable else 'template'
        self.search.ChangeValue('')
        self._populate(selected_paths=[path])

    def _populate(self, selected_paths=None):
        if selected_paths is None:
            selected_paths = [self.comparison.items[i].path for i in self._selected_indices()]
        query = self.search.GetValue().strip().casefold()
        documents = self.category == 'documents'
        self.visible_indices = [i for i, item in enumerate(self.comparison.items)
                                if item.editable == documents and query in item.path.casefold()]
        self.files.Freeze()
        try:
            self.files.DeleteAllItems()
            for row, index in enumerate(self.visible_indices):
                item = self.comparison.items[index]
                self.files.InsertItem(row, Path(item.path).name)
                self.files.SetItem(row, 1, self._state_label(item))
                self.files.SetItem(row, 2, self._action_label(self.choices[item.path]))
                self.files.SetItemTextColour(row, item_colour(item))
                if item.path in selected_paths:
                    self.files.Select(row)
            if self.visible_indices and self.files.GetFirstSelected() == -1:
                self.files.Select(0)
        finally:
            self.files.Thaw()
        count = sum(item.editable for item in self.comparison.items)
        self.documents_button.SetLabel(f'Project documents ({count})')
        self.template_button.SetLabel(f'Template files ({len(self.comparison.items) - count})')
        self.documents_button.SetValue(documents)
        self.template_button.SetValue(not documents)
        self._update_pending()
        self._refresh_selection()

    def _update_pending(self):
        writes = sum(choice in ('use', 'edit') for choice in self.choices.values())
        pending = sum(choice == 'review' for choice in self.choices.values())
        self.pending_label.SetLabel(f'{writes} to write  ·  {pending} left pending')

    def _select(self, event):
        # Native Shift/Ctrl selection emits several intermediate events.
        if not self._selection_pending:
            self._selection_pending = True
            wx.CallAfter(self._refresh_selection)
        event.Skip()

    def _refresh_selection(self):
        self._selection_pending = False
        if not self or self.IsBeingDeleted():
            return
        self._flush_table()
        indices = self._selected_indices()
        if len(indices) == 1:
            self._load(indices[0])
            return
        self.selected = -1
        self._table_path = None
        items = [self.comparison.items[index] for index in indices]
        self.selection_label.SetLabel(f'{len(items)} selected files' if items else 'No files selected')
        self.path_label.SetLabel('Choose one action for the highlighted files.' if items else 'Try another search.')
        self.actions = ['review', 'use'] if items else []
        if items and all(item.current is not None for item in items):
            self.actions.insert(1, 'keep')
        self.action.Set([self._action_label(action) for action in self.actions])
        self.action.Enable(bool(items))
        choices = {self.choices[item.path] for item in items}
        choice = next(iter(choices)) if len(choices) == 1 else None
        self.action.SetSelection(self.actions.index(choice) if choice in self.actions else wx.NOT_FOUND)
        self._set_diff('\n\n'.join(f'{item.path}\n{item.status} · {self._action_label(self.choices[item.path])}'
                                   for item in items) if items else 'No files match this search.')
        self.editor.ChangeValue('')
        self.editor.Disable()
        self.edit_button.Hide()
        self.source_button.Hide()
        self.diff_mode.Disable()
        self.changes_button.SetValue(True)
        self.views.SetSelection(1)
        self._set_context('Choices and unsaved edits stay staged when you switch lists.')
        self._right.Layout()
        self.Layout()

    def _load(self, index):
        self.selected = index
        item = self.comparison.items[index]
        self.selection_label.SetLabel(Path(item.path).name)
        self.path_label.SetLabel(item.path)
        self.path_label.SetToolTip(item.path)
        self.actions = ['review', 'keep', 'use'] if item.current is not None else ['review', 'use']
        if item.editable:
            self.actions.append('edit')
        self.action.Set([self._action_label(action) for action in self.actions])
        self.action.Enable()
        self.action.SetSelection(self.actions.index(self.choices[item.path]))
        self._update_diff(item)
        self._loading = True
        try:
            self.editor.ChangeValue(item.editor_text(self.edits.get(item.path)) if item.editable else '')
            self.editor.Enable(item.editable)
            self.shared_toggle.Show(item.kind == 'ignore_rules')
            self.user_rules_label.Show(item.kind == 'ignore_rules')
            if item.kind == 'ignore_rules':
                shared, _ = split_ignore_rules(text(item.current or b''))
                self.shared_rules.ChangeValue(shared or split_ignore_rules(text(item.incoming), required=True)[0])
        finally:
            self._loading = False
        self.edit_button.Show(item.editable)
        self._table_available = False
        self._table_path = None
        if item.kind == 'document':
            try:
                table = ImpedanceTable.parse(item.editor_text(self.edits.get(item.path)))
                self.impedance_panel.load(table)
                self._table_available = True
                self._table_path = item.path
            except SetupError:
                pass  # Other document formats keep the ordinary text editor.
        self.edit_button.SetLabel('Table' if self._table_available else 'Edit')
        self.source_button.Show(self._table_available)
        self._toggle_shared_rules(None)
        self._show_view(self._document_view if item.editable else 1, remember=False)
        self.Layout()

    def _show_view(self, view, *, remember=True):
        self._flush_table()
        editable = self.selected >= 0 and self.comparison.items[self.selected].editable
        if editable and remember:
            self._document_view = view
        view = view if editable else 1
        if view == 2 and not self._table_available:
            view = 0
        self.edit_button.SetValue(view == 0)
        self.source_button.SetValue(view == 2)
        self.changes_button.SetValue(view == 1)
        if view == 0 and self._table_available:
            try:
                item = self.comparison.items[self.selected]
                self.impedance_panel.load(ImpedanceTable.parse(item.editor_text(self.edits.get(item.path))))
            except SetupError as exc:
                wx.MessageBox(str(exc), 'Impedance table format', wx.OK | wx.ICON_ERROR, self)
                view = 2
                self.edit_button.SetValue(False)
                self.source_button.SetValue(True)
        self.views.SetSelection(2 if view == 0 and self._table_available else 0 if view == 2 else view)
        self.views.GetCurrentPage().Layout()
        if self.selected >= 0:
            item = self.comparison.items[self.selected]
            if view == 1:
                self._update_diff(item)
                note = ('Reference comparison only. Red: only in project; green: only in template. '
                        'The chosen action decides what is saved.' if self.diff_mode.GetSelection() == 0 else
                        'Planned changes for this file. Red: removed on Apply; green: written on Apply.')
            elif item.kind == 'ignore_rules':
                note = 'Edit project rules below. Use template updates shared rules and keeps your rules.'
            elif item.path == README_SOURCE:
                note = 'Edit the README source. CI regenerates README.md on its next run.'
            elif self._table_available:
                note = 'Edit cells in Table, or use Text for the CSV source. Apply saves the document.'
            else:
                note = 'Edit this project document. Apply saves it as an accepted customization.'
            self._set_context(note)
        self._right.Layout()
        self.Layout()

    def _set_context(self, note):
        self._context_note = note
        self.context_label.SetLabel(note)
        self.context_label.Wrap(max(260, self._right.GetClientSize().width - 12))

    def _resize_context(self, event):
        if hasattr(self, 'context_label'):
            self._set_context(self._context_note)
            self._right.Layout()
        event.Skip()

    def _close(self, event):
        self._flush_table()
        drafts = [item.path for item in self.comparison.items if item.path in self._edited_paths
                  and self.choices[item.path] in ('edit', 'review')
                  and (item.current is None or text(self.edits[item.path]) != text(item.current))]
        if drafts and wx.MessageBox('Discard unapplied document edits?\n\n' + '\n'.join(drafts),
                                   'Unsaved project documents', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
                                   self) != wx.YES:
            return
        event.Skip()

    def _toggle_shared_rules(self, event):
        show = (self.selected >= 0 and self.comparison.items[self.selected].kind == 'ignore_rules'
                and self.shared_toggle.GetValue())
        if show and not self.ignore_splitter.IsSplit():
            self.shared_panel.Show()
            self.ignore_splitter.SplitHorizontally(self.shared_panel, self._user_panel)
            self.ignore_splitter.SetSashPosition(max(90, self.ignore_splitter.GetClientSize().height // 2))
        elif not show and self.ignore_splitter.IsSplit():
            self.ignore_splitter.Unsplit(self.shared_panel)
        self._user_panel.Layout()
        self.views.GetCurrentPage().Layout()

    def _set_diff(self, value):
        show_diff(self.diff, value)

    def _diff_mode_changed(self, event):
        self._show_view(1)

    def _update_diff(self, item):
        self.diff_mode.Enable(True)
        if self.diff_mode.GetSelection() == 0:
            self._set_diff(item.diff())
            return
        choice = self.choices[item.path]
        if choice in ('keep', 'review'):
            self._set_diff('This project file will stay unchanged.' + (
                '\nIts review decision may be recorded.' if choice == 'keep' else '\nThe file is left pending.'))
            return
        try:
            plan = prepare_upgrade(self.comparison, {item.path: choice}, self.edits)
            self._set_diff(Upgrade(self.comparison, tuple(change for change in plan.changes
                                                        if change.relative == item.path)).diff())
        except SetupError as exc:
            self._set_diff(f'Cannot prepare this action: {exc}')

    def _refresh(self, event):
        self._flush_table()
        try:
            fresh = compare(self.comparison.root, load_template(self.comparison.template.root))
            previous = {item.path: item for item in self.comparison.items}
            compatible = {item.path for item in fresh.items if item.path in previous
                          and (item.current, item.incoming, item.kind, item.base) == (
                              previous[item.path].current, previous[item.path].incoming,
                              previous[item.path].kind, previous[item.path].base)}
            changed_drafts = sorted(self._edited_paths - compatible)
            if changed_drafts and wx.MessageBox(
                    'These files changed on disk or in the template. Refresh will discard their unapplied edits; '
                    'other drafts will be kept.\n\n' + '\n'.join(changed_drafts),
                    'Refresh changed documents', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self) != wx.YES:
                return
            selected_paths = [self.comparison.items[i].path for i in self._selected_indices()]
            self.choices = {item.path: self.choices[item.path] if item.path in compatible else
                            'keep' if item.status == 'Matches' or item.accepted else 'review' for item in fresh.items}
            self.edits = {**{item.path: item.merged for item in fresh.items if item.merged is not None},
                          **{path: data for path, data in self.edits.items() if path in compatible}}
            self._edited_paths.intersection_update(compatible)
            changed = len(set(previous) | {item.path for item in fresh.items}) - len(compatible)
            self.comparison = fresh
            self.refreshed = True
            self.impedance_panel.refresh_guides()
            self.intro.SetLabel(self._intro())
            self._populate(selected_paths)
            self.feedback.SetLabel(f'Refreshed. {changed} changed file(s) rechecked; other choices and drafts kept.')
            self.feedback.SetForegroundColour(MATCH_COLOUR)
            self.Layout()
        except (SetupError, OSError) as exc:
            wx.MessageBox(str(exc), 'Refresh template review', wx.OK | wx.ICON_ERROR, self)

    def _action_changed(self, event):
        self._flush_table()
        selection = self.action.GetSelection()
        if selection == wx.NOT_FOUND:
            return
        action = self.actions[selection]
        indices = self._selected_indices()
        if (not indices or (action == 'edit' and indices != [self.selected])
                or (action == 'keep' and any(self.comparison.items[i].current is None for i in indices))):
            self._refresh_selection()
            return
        for index in indices:
            item = self.comparison.items[index]
            self.choices[item.path] = action
            if action == 'edit':
                self.edits[item.path] = item.edited_content(self.editor.GetValue())
                self._edited_paths.add(item.path)
                self._document_view = 0
        self._populate()

    def _edited(self, event):
        if self._loading or self.selected < 0 or self._selected_indices() != [self.selected]:
            return
        item = self.comparison.items[self.selected]
        if not item.editable:
            return
        self.edits[item.path] = item.edited_content(self.editor.GetValue())
        self._edited_paths.add(item.path)
        self.choices[item.path] = 'edit'
        self.action.SetSelection(self.actions.index('edit'))
        row = self.visible_indices.index(self.selected)
        self.files.SetItem(row, 2, self._action_label('edit'))
        self._update_pending()

    def _flush_table(self):
        if self._table_path is not None and self.views.GetSelection() == 2:
            self.impedance_panel.flush()

    def _table_changed(self, value):
        if self._loading or self._table_path is None:
            return
        item = next(item for item in self.comparison.items if item.path == self._table_path)
        self.edits[item.path] = item.edited_content(value)
        self._edited_paths.add(item.path)
        self.choices[item.path] = 'edit'
        self.editor.ChangeValue(value)
        if self.selected >= 0 and self.comparison.items[self.selected].path == item.path:
            self.action.SetSelection(self.actions.index('edit'))
        index = next(i for i, candidate in enumerate(self.comparison.items) if candidate.path == item.path)
        if index in self.visible_indices:
            self.files.SetItem(self.visible_indices.index(index), 2, self._action_label('edit'))
        self._update_pending()

    def _preview(self, event):
        self._flush_table()
        try:
            choices = dict(self.choices)
            if not self._confirm_documents(choices):
                return
            plan = prepare_upgrade(self.comparison, choices, self.edits)
            deferred = sum(choice == 'review' for choice in choices.values())
            with PreviewDialog(self, plan, title='Apply Template Changes',
                               intro=f'Apply the chosen actions? {deferred} file(s) left pending will stay unchanged. '
                               'Original files are backed up locally. '
                               'Board and schematic contents are not changed.') as dialog:
                if dialog.ShowModal() != wx.ID_OK:
                    return
            self._apply_reviewed(plan, choices)
        except (SetupError, OSError) as exc:
            wx.MessageBox(str(exc), 'Template upgrade', wx.OK | wx.ICON_ERROR, self)

    def _apply(self, event):
        self._flush_table()
        self._apply_choices(dict(self.choices))

    def _confirm_documents(self, choices):
        replacements = [item.path for item in self.comparison.items
                        if choices.get(item.path) == 'use' and item.kind == 'document'
                        and item.current is not None and item.current != item.incoming]
        if not replacements:
            return True
        message = ('Replace these project documents with the installed template defaults?\n\n'
                   + '\n'.join(replacements) + '\n\nYour current files will be backed up.')
        if README_SOURCE in replacements:
            message += (' The next CI run will overwrite README.md using this default. '
                        'You can edit readme.txt again afterwards.')
        return wx.MessageBox(
            message, 'Use template documents', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self) == wx.YES

    def _apply_choices(self, choices):
        try:
            if not self._confirm_documents(choices):
                return
            plan = prepare_upgrade(self.comparison, choices, self.edits)
            self._apply_reviewed(plan, choices)
        except (SetupError, OSError) as exc:
            wx.MessageBox(str(exc), 'Template upgrade', wx.OK | wx.ICON_ERROR, self)

    def _apply_reviewed(self, plan, choices):
        """Save exactly the reviewed plan and refresh without closing this dialog."""
        backup = self._apply_plan(plan)
        self.applied = True
        selected_paths = [self.comparison.items[i].path for i in self._selected_indices()]
        comparison = compare(plan.comparison.root, plan.comparison.template)
        pending_edits = {path: data for path, data in self.edits.items()
                         if choices.get(path, 'review') == 'review'}
        self.choices = {item.path: self.choices[item.path] if choices.get(item.path, 'review') == 'review' else 'keep'
                        for item in comparison.items}
        self.edits = {item.path: item.merged for item in comparison.items if item.merged is not None}
        self.edits.update(pending_edits)
        self._edited_paths.intersection_update(pending_edits)
        self.comparison = comparison
        self.intro.SetLabel(self._intro())
        self.intro.Wrap(max(200, self.GetClientSize().width - 24))
        self._populate(selected_paths)
        self.feedback.SetLabel(f'Changes applied. Backup: {backup}' if backup else 'The chosen actions are already applied.')
        self.feedback.Wrap(max(200, self.GetClientSize().width - 24))
        self.Layout()


class TemplateDetailsDialog(wx.Dialog):
    def __init__(self, parent, comparison: Comparison):
        super().__init__(parent, title='Template Details', size=(900, 620),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        layout = wx.BoxSizer(wx.VERTICAL)
        self.results = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL | wx.TE_RICH2)
        neutral = self.results.GetForegroundColour()
        self.results.AppendText(f'{comparison.summary}\n\nTemplate: {comparison.template.root}\n\n')
        for item in comparison.items:
            self.results.SetDefaultStyle(wx.TextAttr(item_colour(item)))
            self.results.AppendText(f'{item.status}: {item.path}\n')
        self.results.SetDefaultStyle(wx.TextAttr(neutral))
        if comparison.extras:
            self.results.AppendText('\nAdditional project files (preserved):\n' + '\n'.join(comparison.extras))
        self.results.ShowPosition(0)
        layout.Add(self.results, 1, wx.EXPAND | wx.ALL, 12)
        layout.Add(self.CreateStdDialogButtonSizer(wx.OK), 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.CentreOnParent()


def show_details(parent, comparison: Comparison):
    with TemplateDetailsDialog(parent, comparison) as dialog:
        dialog.ShowModal()


def show_text_details(parent, message: str):
    with wx.Dialog(parent, title='Template Details', size=(900, 620),
                   style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER) as dialog:
        layout = wx.BoxSizer(wx.VERTICAL)
        control = wx.TextCtrl(dialog, value=message, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        layout.Add(control, 1, wx.EXPAND | wx.ALL, 12)
        layout.Add(dialog.CreateStdDialogButtonSizer(wx.OK), 0, wx.EXPAND | wx.ALL, 12)
        dialog.SetSizer(layout)
        dialog.CentreOnParent()
        dialog.ShowModal()
