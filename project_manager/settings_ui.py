"""Choose and validate the shared standards repository."""
from __future__ import annotations

from pathlib import Path
import wx

from .preferences import load_standards, save_settings
from .standards import load_catalog


class StandardsFolderDialog(wx.Dialog):
    def __init__(self, parent, storage: Path | None = None):
        super().__init__(parent, title='Standards Repository', size=(740, 280),
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

        layout.AddStretchSpacer()
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        self.FindWindowById(wx.ID_OK).SetLabel('Use Folder')
        self.Bind(wx.EVT_BUTTON, self._save, id=wx.ID_OK)
        layout.Add(buttons, 0, wx.EXPAND | wx.ALL, 14)
        self.SetSizer(layout)
        self.SetMinSize((680, 280))
        self.Fit()
        self.CentreOnParent()

    def _save(self, event):
        try:
            value = self.folder.GetPath().strip()
            if value:
                value = str(load_catalog(Path(value)).root)
            save_settings({'standards_directory': value}, self.storage)
        except (OSError, ValueError) as exc:
            wx.MessageBox(str(exc), 'Standards repository', wx.OK | wx.ICON_ERROR, self)
            return
        self.EndModal(wx.ID_OK)
