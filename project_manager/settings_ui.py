"""Per-computer standards source and backup location."""
from __future__ import annotations

from pathlib import Path
import wx

from .preferences import data_directory, load_standards, save_settings
from .standards import load_catalog


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, storage: Path | None = None):
        super().__init__(parent, title='Project Manager Settings', size=(740, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.storage = storage
        layout = wx.BoxSizer(wx.VERTICAL)
        box = wx.StaticBoxSizer(wx.VERTICAL, self, 'Standards Folder')
        text = wx.StaticText(box.GetStaticBox(), label='Choose the folder containing catalog.json and your reusable rules and stackups.\nThis setting is shared by your projects on this computer.')
        text.Wrap(660)
        box.Add(text, 0, wx.ALL, 12)
        self.folder = wx.DirPickerCtrl(box.GetStaticBox(), path=load_standards(storage),
                                       message='Choose your standards folder',
                                       style=wx.DIRP_DEFAULT_STYLE | wx.DIRP_USE_TEXTCTRL)
        box.Add(self.folder, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        hint = wx.StaticText(box.GetStaticBox(), label='A local folder or an existing Git checkout both work. Git download/update controls will be added later.')
        hint.Wrap(660)
        box.Add(hint, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        layout.Add(box, 0, wx.EXPAND | wx.ALL, 14)

        box = wx.StaticBoxSizer(wx.VERTICAL, self, 'Project Backups')
        self.backup_path = (storage or data_directory()) / 'backups'
        text = wx.TextCtrl(box.GetStaticBox(), value=str(self.backup_path), style=wx.TE_READONLY)
        box.Add(text, 0, wx.EXPAND | wx.ALL, 12)
        hint = wx.StaticText(box.GetStaticBox(), label='Original files are grouped by project, then by save time. Each backup includes a manifest identifying the project and changed files.')
        hint.Wrap(660)
        box.Add(hint, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.backups_button = wx.Button(box.GetStaticBox(), label='Open Backups')
        self.backups_button.Enable(self.backup_path.is_dir())
        self.backups_button.SetToolTip('Available after the first saved project change.')
        self.backups_button.Bind(wx.EVT_BUTTON, self._open_backups)
        box.Add(self.backups_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        layout.Add(box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        layout.AddStretchSpacer()
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        self.FindWindowById(wx.ID_OK).SetLabel('Save Settings')
        self.Bind(wx.EVT_BUTTON, self._save, id=wx.ID_OK)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 14)
        self.SetSizer(layout)
        self.SetMinSize((680, 440))
        self.Fit()
        self.CentreOnParent()

    def _save(self, event):
        try:
            value = self.folder.GetPath().strip()
            if value:
                value = str(load_catalog(Path(value)).root)
            save_settings({'standards_directory': value}, self.storage)
        except (OSError, ValueError) as exc:
            wx.MessageBox(str(exc), 'Settings', wx.OK | wx.ICON_ERROR, self)
            return
        self.EndModal(wx.ID_OK)

    def _open_backups(self, event):
        if not wx.LaunchDefaultApplication(str(self.backup_path)):
            wx.MessageBox(f'Could not open the folder. Copy this path into your file manager:\n{self.backup_path}',
                          'Backups', wx.OK | wx.ICON_ERROR, self)
