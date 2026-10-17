"""Native template comparison and per-file migration review."""
from __future__ import annotations

from pathlib import Path
import wx

from .setup_ui import PreviewDialog
from .templates import Comparison, Item, policy, prepare_upgrade, text
from .yaml_fields import SetupError

MATCH_COLOUR = wx.Colour(46, 170, 80)
MODIFIED_COLOUR = wx.Colour(220, 160, 50)
MISSING_COLOUR = wx.Colour(220, 70, 70)


def item_colour(item: Item) -> wx.Colour:
    if item.current is None:
        return MISSING_COLOUR
    return MATCH_COLOUR if item.status in ('Matches', 'Project content preserved') else MODIFIED_COLOUR


class TemplateReviewDialog(wx.Dialog):
    def __init__(self, parent, comparison: Comparison):
        super().__init__(parent, title='Review Template Upgrade', size=(1000, 760),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.comparison = comparison
        self.choices = {item.path: item.suggested for item in comparison.items}
        self.edits: dict[str, bytes] = {item.path: item.merged for item in comparison.items if item.merged is not None}
        self.selected = -1
        self.plan = None
        layout = wx.BoxSizer(wx.VERTICAL)
        intro = wx.StaticText(self, label=comparison.summary + '\n'
                              'Review each file. Metadata, release history and workflow status are preserved. '
                              'Keeping project documentation accepts your content for this template release. '
                              'Shared-file modifications remain visible after applying.')
        intro.Wrap(930)
        layout.Add(intro, 0, wx.EXPAND | wx.ALL, 12)
        self.files = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(-1, 210))
        for index, (label, width) in enumerate((('File', 470), ('Comparison', 310), ('Action', 140))):
            self.files.InsertColumn(index, label, width=width)
        for index, item in enumerate(comparison.items):
            self.files.InsertItem(index, item.path)
            self.files.SetItem(index, 1, item.status)
            self.files.SetItem(index, 2, self._action_label(item.suggested))
            self.files.SetItemTextColour(index, item_colour(item))
        self.files.Bind(wx.EVT_LIST_ITEM_SELECTED, self._select)
        layout.Add(self.files, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, label='Selected file:'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
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
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        self.FindWindow(wx.ID_OK).SetLabel('Preview Apply…')
        self.Bind(wx.EVT_BUTTON, self._preview, id=wx.ID_OK)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(layout)
        self.SetMinSize((820, 620))
        self.CentreOnParent()
        if comparison.items:
            self.files.Select(0)
            self._load(0)

    @staticmethod
    def _action_label(action: str) -> str:
        return {'review': 'Choose action…', 'keep': 'Keep project',
                'use': 'Use template', 'edit': 'Edit document'}[action]

    def _select(self, event):
        self._load(event.GetIndex())

    def _load(self, index):
        self.selected = index
        item = self.comparison.items[index]
        self.actions = ['keep', 'use'] if item.current is not None else ['use']
        if item.suggested == 'review':
            self.actions.insert(0, 'review')
        document = policy(item.path) == 'document'
        if document:
            self.actions.append('edit')
        self.action.Set([self._action_label(a) for a in self.actions])
        self.action.SetSelection(self.actions.index(self.choices[item.path]))
        self.diff.ChangeValue(item.diff())
        self.editor.ChangeValue(text(self.edits.get(item.path, item.current or item.incoming)) if document else '')
        self.editor.Enable(document)
        self.history.ChangeValue((item.base or {}).get('content', 'No previous document baseline is available.')
                                 if document else 'Baseline content is only stored for project documentation.')

    def _action_changed(self, event):
        item = self.comparison.items[self.selected]
        action = self.actions[self.action.GetSelection()]
        self.choices[item.path] = action
        if action == 'edit':
            self.edits[item.path] = self.editor.GetValue().encode('utf-8')
            self.views.SetSelection(1)
        self.files.SetItem(self.selected, 2, self._action_label(action))

    def _edited(self, event):
        if self.selected < 0:
            return
        item = self.comparison.items[self.selected]
        self.edits[item.path] = self.editor.GetValue().encode('utf-8')
        self.choices[item.path] = 'edit'
        self.action.SetSelection(self.actions.index('edit'))
        self.files.SetItem(self.selected, 2, self._action_label('edit'))

    def _preview(self, event):
        try:
            plan = prepare_upgrade(self.comparison, self.choices, self.edits)
            with PreviewDialog(self, plan, title='Apply Template Changes',
                               intro='Apply this reviewed migration? Original files are backed up locally. '
                               'Board and schematic contents are not changed.') as dialog:
                if dialog.ShowModal() != wx.ID_OK:
                    return
            self.plan = plan
            self.EndModal(wx.ID_OK)
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
