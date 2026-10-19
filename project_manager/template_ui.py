"""Native template comparison and per-file migration review."""
from __future__ import annotations

from pathlib import Path
from typing import Callable
import wx

from .setup_ui import PreviewDialog
from .templates import Comparison, Item, Upgrade, apply_upgrade, compare, policy, prepare_upgrade, text
from .yaml_fields import SetupError

MATCH_COLOUR = wx.Colour(46, 170, 80)
MODIFIED_COLOUR = wx.Colour(220, 160, 50)
MISSING_COLOUR = wx.Colour(220, 70, 70)


def item_colour(item: Item) -> wx.Colour:
    if item.current is None:
        return MISSING_COLOUR
    return MATCH_COLOUR if item.status in ('Matches', 'Project content preserved') else MODIFIED_COLOUR


class TemplateReviewDialog(wx.Dialog):
    def __init__(self, parent, comparison: Comparison,
                 apply_plan: Callable[[Upgrade], Path | None] | None = None):
        super().__init__(parent, title='Review Template Upgrade', size=(1000, 760),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.comparison = comparison
        self.choices = {item.path: 'keep' if item.status in ('Matches', 'Project content preserved') else 'review'
                        for item in comparison.items}
        self.edits: dict[str, bytes] = {item.path: item.merged for item in comparison.items if item.merged is not None}
        self.selected = -1
        self._selection_pending = False
        self._apply_plan = apply_plan or apply_upgrade
        self.applied = False
        self.plan = None
        layout = wx.BoxSizer(wx.VERTICAL)
        self.intro = wx.StaticText(self, label=self._intro())
        self.intro.Wrap(930)
        layout.Add(self.intro, 0, wx.EXPAND | wx.ALL, 12)
        self.files = wx.ListCtrl(self, style=wx.LC_REPORT, size=(-1, 210))
        for index, (label, width) in enumerate((('File', 470), ('Comparison', 310), ('Action', 140))):
            self.files.InsertColumn(index, label, width=width)
        for index, item in enumerate(comparison.items):
            self.files.InsertItem(index, item.path)
            self.files.SetItem(index, 1, item.status)
            self.files.SetItem(index, 2, self._action_label(self.choices[item.path]))
            self.files.SetItemTextColour(index, item_colour(item))
        self.files.Bind(wx.EVT_LIST_ITEM_SELECTED, self._select)
        self.files.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._select)
        layout.Add(self.files, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.selection_label = wx.StaticText(self, label='Selected file:')
        self.selection_label.SetMinSize((self.selection_label.GetTextExtent('1000 selected files:').width, -1))
        row.Add(self.selection_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.action = wx.Choice(self)
        self.action.Bind(wx.EVT_CHOICE, self._action_changed)
        row.Add(self.action, 1)
        layout.Add(row, 0, wx.EXPAND | wx.ALL, 12)
        self.views = wx.Notebook(self)
        self.diff = wx.TextCtrl(self.views, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.editor = wx.TextCtrl(self.views, style=wx.TE_MULTILINE | wx.HSCROLL)
        self.history = wx.TextCtrl(self.views, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        for control in (self.diff, self.editor, self.history):
            control.SetFont(wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE)))
        self.views.AddPage(self.diff, 'Project → template diff')
        self.views.AddPage(self.editor, 'Edit project document')
        self.views.AddPage(self.history, 'Previous document baseline')
        self.editor.Bind(wx.EVT_TEXT, self._edited)
        layout.Add(self.views, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        if comparison.extras:
            extra = wx.StaticText(self, label=f'{len(comparison.extras)} additional project file(s) will be preserved. '
                                  'See the file list in Details.')
            layout.Add(extra, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.feedback = wx.StaticText(self, label='')
        layout.Add(self.feedback, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer()
        buttons.Add(wx.Button(self, wx.ID_CANCEL, 'Close'))
        self.preview_button = wx.Button(self, wx.ID_OK, 'Preview Apply…')
        self.Bind(wx.EVT_BUTTON, self._preview, id=wx.ID_OK)
        buttons.Add(self.preview_button, 0, wx.LEFT, 8)
        self.apply_button = wx.Button(self, wx.ID_APPLY, 'Apply')
        self.apply_button.SetToolTip('Apply the chosen actions and continue reviewing. Original files are backed up locally.')
        self.apply_button.Bind(wx.EVT_BUTTON, self._apply)
        buttons.Add(self.apply_button, 0, wx.LEFT, 8)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetMinSize((820, 620))
        self.CentreOnParent()
        if comparison.items:
            self.files.Select(0)
        self._refresh_selection()

    def _intro(self):
        return (self.comparison.summary + '\n'
                'Shift-click to select a range; Ctrl-click to select individual files. '
                'Files left at “Choose action…” stay unchanged. Apply saves and keeps this window open. '
                'Metadata, release history and workflow status are preserved. '
                'Keeping project documentation accepts your content for this template release. '
                'Shared-file modifications remain visible after applying.')

    @staticmethod
    def _action_label(action: str) -> str:
        return {'review': 'Choose action…', 'keep': 'Keep project',
                'use': 'Use template', 'edit': 'Edit document'}[action]

    def _select(self, event):
        # A range selection emits several select/deselect events. Read the final
        # selection once, after the native control has processed the whole range.
        if not self._selection_pending:
            self._selection_pending = True
            wx.CallAfter(self._refresh_selection)
        event.Skip()

    def _selected_indices(self):
        indices = []
        index = self.files.GetFirstSelected()
        while index != -1:
            indices.append(index)
            index = self.files.GetNextSelected(index)
        return indices

    def _refresh_selection(self):
        self._selection_pending = False
        if not self or self.IsBeingDeleted():
            return
        indices = self._selected_indices()
        if len(indices) == 1:
            self._load(indices[0])
            return
        self.selected = -1
        items = [self.comparison.items[index] for index in indices]
        self.selection_label.SetLabel(f'{len(items)} selected files:' if items else 'No files selected:')
        self.actions = ['review', 'use'] if items else []
        if items and all(item.current is not None for item in items):
            self.actions.insert(1, 'keep')
        self.action.Set([self._action_label(action) for action in self.actions])
        self.action.Enable(bool(items))
        choices = {self.choices[item.path] for item in items}
        choice = next(iter(choices)) if len(choices) == 1 else None
        self.action.SetSelection(self.actions.index(choice) if choice in self.actions else wx.NOT_FOUND)
        self.diff.ChangeValue('Choose an action to apply to all highlighted files.\n\n' + '\n'.join(
            f'{item.path}\n  {item.status} · {self._action_label(self.choices[item.path])}' for item in items)
            if items else 'Select one or more files to review.')
        self.editor.ChangeValue('')
        self.editor.Disable()
        self.history.ChangeValue('Select a single project document to view its previous baseline.')
        self.views.SetSelection(0)
        self.Layout()

    def _load(self, index):
        self.selected = index
        item = self.comparison.items[index]
        self.selection_label.SetLabel('Selected file:')
        self.actions = ['review', 'keep', 'use'] if item.current is not None else ['review', 'use']
        document = policy(item.path) == 'document'
        if document:
            self.actions.append('edit')
        self.action.Set([self._action_label(a) for a in self.actions])
        self.action.Enable()
        self.action.SetSelection(self.actions.index(self.choices[item.path]))
        self.diff.ChangeValue(item.diff())
        self.editor.ChangeValue(text(self.edits.get(item.path, item.current or item.incoming)) if document else '')
        self.editor.Enable(document)
        self.history.ChangeValue((item.base or {}).get('content', 'No previous document baseline is available.')
                                 if document else 'Baseline content is only stored for project documentation.')
        self.Layout()

    def _action_changed(self, event):
        selection = self.action.GetSelection()
        if selection == wx.NOT_FOUND:
            return
        action = self.actions[selection]
        indices = self._selected_indices()
        # Selection notifications are deferred; never let a stale single-file
        # edit action copy one document's contents across a new selection.
        if (not indices
                or (action == 'edit' and indices != [self.selected])
                or (action == 'keep' and any(self.comparison.items[index].current is None for index in indices))):
            self._refresh_selection()
            return
        for index in indices:
            item = self.comparison.items[index]
            self.choices[item.path] = action
            if action == 'edit':
                self.edits[item.path] = self.editor.GetValue().encode('utf-8')
                self.views.SetSelection(1)
            self.files.SetItem(index, 2, self._action_label(action))
        self._refresh_selection()

    def _edited(self, event):
        if self.selected < 0 or self._selected_indices() != [self.selected]:
            return
        item = self.comparison.items[self.selected]
        if policy(item.path) != 'document':
            return
        self.edits[item.path] = self.editor.GetValue().encode('utf-8')
        self.choices[item.path] = 'edit'
        self.action.SetSelection(self.actions.index('edit'))
        self.files.SetItem(self.selected, 2, self._action_label('edit'))

    def _preview(self, event):
        try:
            plan = prepare_upgrade(self.comparison, self.choices, self.edits)
            deferred = sum(self.choices[item.path] == 'review' for item in self.comparison.items)
            with PreviewDialog(self, plan, title='Apply Template Changes',
                               intro=f'Apply the chosen actions? {deferred} file(s) left without an action will stay unchanged. '
                               'Original files are backed up locally. '
                               'Board and schematic contents are not changed.') as dialog:
                if dialog.ShowModal() != wx.ID_OK:
                    return
            self.plan = plan
            self.EndModal(wx.ID_OK)
        except (SetupError, OSError) as exc:
            wx.MessageBox(str(exc), 'Template upgrade', wx.OK | wx.ICON_ERROR, self)

    def _apply(self, event):
        try:
            plan = prepare_upgrade(self.comparison, self.choices, self.edits)
            backup = self._apply_plan(plan)
            self.applied = True
            # Refresh against the reviewed source snapshot. The next apply still
            # verifies the source and every project file before writing.
            comparison = compare(self.comparison.root, self.comparison.template)
            pending_edits = {path: data for path, data in self.edits.items() if self.choices[path] == 'review'}
            self.choices = {item.path: 'review' if self.choices[item.path] == 'review' else 'keep'
                            for item in comparison.items}
            self.edits = {item.path: item.merged for item in comparison.items if item.merged is not None}
            self.edits.update(pending_edits)
            self.comparison = comparison
            self.intro.SetLabel(self._intro())
            self.intro.Wrap(max(200, self.GetClientSize().width - 24))
            for index, item in enumerate(comparison.items):
                self.files.SetItem(index, 1, item.status)
                self.files.SetItem(index, 2, self._action_label(self.choices[item.path]))
                self.files.SetItemTextColour(index, item_colour(item))
            self._refresh_selection()
            self.feedback.SetLabel(f'Changes applied. Backup: {backup}' if backup else 'The chosen actions are already applied.')
            self.feedback.Wrap(max(200, self.GetClientSize().width - 24))
            self.Layout()
        except (SetupError, OSError) as exc:
            wx.MessageBox(str(exc), 'Template upgrade', wx.OK | wx.ICON_ERROR, self)


class TemplateDetailsDialog(wx.Dialog):
    def __init__(self, parent, comparison: Comparison):
        super().__init__(parent, title='Template Details', size=(900, 620),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        layout = wx.BoxSizer(wx.VERTICAL)
        self.results = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL | wx.TE_RICH2)
        neutral = self.results.GetForegroundColour()
        self.results.AppendText(f'{comparison.summary}\n\nTemplate: {comparison.template.root}\n\n')
        for item in comparison.items:
            start = self.results.GetLastPosition()
            self.results.AppendText(f'{item.status}: {item.path}\n')
            # Cocoa can reset typing attributes when AppendText moves the
            # insertion point. Style the inserted native range explicitly.
            self.results.SetStyle(start, self.results.GetLastPosition(), wx.TextAttr(item_colour(item)))
        if comparison.extras:
            start = self.results.GetLastPosition()
            self.results.AppendText('\nAdditional project files (preserved):\n' + '\n'.join(comparison.extras))
            self.results.SetStyle(start, self.results.GetLastPosition(), wx.TextAttr(neutral))
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
