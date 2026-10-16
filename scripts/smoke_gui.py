"""Exercise native GUI refresh/error/close behavior using synthetic project data."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory
from unittest.mock import patch

# GTK/Wayland does not support ClientDC readback. This affects screenshots only.
if sys.platform == "linux" and os.environ.get("DISPLAY"):
    os.environ.setdefault("GDK_BACKEND", "x11")

import wx

from project_manager.context import ContextError, ProjectContext
from project_manager.ui import FOUND_COLOUR, MISSING_COLOUR, ProjectManagerFrame
from project_manager.setup import read_setup
from project_manager.setup_ui import PreviewDialog
from project_manager.preferences import load_standards
from project_manager.settings_ui import SettingsDialog
from tests.fixtures import sample_project
from tests.standards_fixtures import catalog_folder, saved_board


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".dev/gui.png"))
    args = parser.parse_args()
    temporary = TemporaryDirectory()
    base = Path(temporary.name)
    project = sample_project(base / 'project', workflow=False)
    template = sample_project(base / 'template')
    sample = ProjectContext(
        kicad_version="9.0.9", project_name="sample",
        project_directory=str(project),
        project_file=str(project / 'sample.kicad_pro'),
        board_file=str(project / 'sample.kicad_pcb'),
        project_saved=True, board_saved=True, kibot_config_present=True, workflow_present=False,
    )
    standards = catalog_folder(base / 'standards')
    std_project, std_board = saved_board(base / 'standards_project')
    standards_context = ProjectContext(
        kicad_version="9.0.9", project_name="sample", project_directory=str(std_project.parent),
        project_file=str(std_project), board_file=str(std_board),
        project_saved=True, board_saved=True, kibot_config_present=False, workflow_present=False,
    )
    before = {}
    calls = []

    def provider():
        calls.append(1)
        if len(calls) == 2:
            raise ContextError("Test connection interruption. Click Refresh to reconnect.")
        return sample

    app = wx.App(False)
    frame = ProjectManagerFrame(provider, storage=base / 'settings')
    frame.Show()
    started = time.monotonic()
    failures = []
    phase = 0

    def check():
        nonlocal phase, before
        try:
            if time.monotonic() - started > 30:
                raise AssertionError("GUI refresh timed out")
            if frame._busy or not calls:
                wx.CallLater(100, check)
                return
            if phase == 0:
                assert frame.fields["project_name"].GetValue() == "sample"
                assert frame.fields["workflow"].GetValue() == "Not found"
                assert frame.fields["kibot"].GetForegroundColour() == FOUND_COLOUR
                assert frame.fields["workflow"].GetForegroundColour() == MISSING_COLOUR
                assert frame.restore_workflow_button.IsShown()
                assert frame.setup_panel.snapshot is not None
                assert not frame.setup_panel.status_choice.IsEnabled()
                phase = 1
                frame.refresh()
            elif phase == 1:
                assert frame._context is None
                assert frame.fields["board_file"].GetValue() == "—"
                assert "Test connection interruption" in frame.log.GetValue()
                assert not frame.setup_panel.preview_button.IsEnabled()
                assert not frame.setup_panel.apply_button.IsEnabled()
                phase = 2
                frame.refresh()
            elif phase == 2:
                assert frame._context == sample
                assert frame.refresh_button.IsEnabled()
                panel = frame.setup_panel
                panel.fields['PROJECT_NAME'].SetValue('Example controller')
                panel.fields['BOARD_NAME'].SetValue('Control board')
                panel.fields['DESIGNER'].SetValue('Alice Martin\nZoë Chen')
                panel.fields['GIT_URL'].SetValue('https://example.org/team/controller')
                assert not panel.status_choice.IsEnabled()
                assert panel.dirty
                before = read_setup(project)
                plan = panel.make_plan()
                with PreviewDialog(panel, plan) as dialog:
                    wx.CallLater(120, lambda: dialog.EndModal(wx.ID_CANCEL))
                    assert dialog.ShowModal() == wx.ID_CANCEL
                assert read_setup(project) == before
                def accept_preview():
                    dialogs = [w for w in wx.GetTopLevelWindows() if isinstance(w, PreviewDialog)]
                    assert len(dialogs) == 1
                    dialogs[0].EndModal(wx.ID_OK)
                wx.CallLater(150, accept_preview)
                panel._preview(None)
                phase = 3
            elif phase == 3:
                saved = read_setup(project)
                assert saved.metadata['DESIGNER'] == 'Alice Martin; Zoë Chen'
                assert saved.status == 'DRAFT'
                assert saved.workflow is None
                seen = {}

                class FakeDirDialog:
                    def __init__(self, parent, message='', defaultPath='', style=0, **kwargs):
                        seen['message'] = message
                        seen['path'] = defaultPath
                    def ShowModal(self):
                        return wx.ID_OK
                    def GetPath(self):
                        return str(template)
                    def Destroy(self):
                        return None

                with patch('project_manager.ui.kicad_user_template_directory', return_value=template.parent), \
                     patch('project_manager.ui.wx.DirDialog', FakeDirDialog):
                    frame._restore_workflow(None)
                assert seen['path'] == str(template.parent)
                assert 'ci.yaml' in seen['message']
                saved = read_setup(project)
                assert b'kibot_variant: DRAFT  # existing comment' in saved.workflow
                assert frame.fields['workflow'].GetValue() == 'Found'
                assert frame.fields['workflow'].GetForegroundColour() == FOUND_COLOUR
                assert not frame.restore_workflow_button.IsShown()
                panel = frame.setup_panel
                assert panel.status_choice.IsEnabled()
                assert 'schematic PDF' in panel.status_choice.GetString(0)
                panel.select_status('PRELIMINARY')
                event = wx.CommandEvent(wx.EVT_BUTTON.typeId, panel.apply_button.GetId())
                panel.apply_button.GetEventHandler().ProcessEvent(event)
                phase = 4
            elif phase == 4:
                saved = read_setup(project)
                assert saved.status == 'PRELIMINARY'
                assert not frame.setup_panel.dirty
                panel = frame.setup_panel
                panel.select_status('CHECKED')
                # Exercise the actual Apply binding: no preview or confirmation.
                with patch('project_manager.setup_ui.PreviewDialog', side_effect=AssertionError('Unexpected preview')), \
                     patch('project_manager.setup_ui.wx.MessageBox', side_effect=AssertionError('Unexpected confirmation')):
                    event = wx.CommandEvent(wx.EVT_BUTTON.typeId, panel.apply_button.GetId())
                    panel.apply_button.GetEventHandler().ProcessEvent(event)
                phase = 5
            elif phase == 5:
                saved = read_setup(project)
                assert saved.status == 'CHECKED'
                assert b'kibot_variant: CHECKED  # existing comment' in saved.workflow
                assert not frame.setup_panel.dirty
                assert not frame.setup_panel.make_plan().changes
                assert len(list((base / 'settings').rglob('manifest.json'))) == 4
                # Settings dialog: save the standards folder through the real OK binding.
                def save_dialog():
                    dialogs = [w for w in wx.GetTopLevelWindows() if isinstance(w, SettingsDialog)]
                    assert len(dialogs) == 1
                    dialog = dialogs[0]
                    assert dialog.backup_path == base / 'settings' / 'backups'
                    assert dialog.backups_button.IsEnabled()
                    dialog.folder.SetPath(str(standards))
                    event = wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK)
                    dialog.GetEventHandler().ProcessEvent(event)
                wx.CallLater(150, save_dialog)
                frame.open_settings()
                assert load_standards(base / 'settings') == str(standards.resolve())
                frame.notebook.SetSelection(2)
                phase = 6
            elif phase == 6:
                panel = frame.standards_panel
                if panel._busy:
                    wx.CallLater(100, check)
                    return
                assert panel.catalog is not None and panel.catalog.name == 'Example standards'
                assert not panel.compare_button.IsEnabled()
                panel.choices['rules'].SetSelection(1)
                panel.choices['stackups'].SetSelection(1)
                panel._selection_changed()
                assert 'Example rules' in panel.details.GetValue()
                panel.set_context(standards_context)
                assert panel.compare_button.IsEnabled()
                before = {p: p.read_bytes() for p in (std_project, std_board, std_board.with_suffix('.kicad_dru'))}
                panel._compare(None)
                phase = 7
            else:
                panel = frame.standards_panel
                if panel._busy:
                    wx.CallLater(100, check)
                    return
                assert 'Rules: Example rules' in panel.summary.GetValue(), panel.summary.GetValue()
                assert 'Protected' not in panel.summary.GetValue().split('missing from the saved project: ')[-1]
                assert 'min_clearance' in panel.diff.GetValue()
                assert 'company-rules block' in panel.summary.GetValue()
                if phase == 7:
                    assert all(p.read_bytes() == data for p, data in before.items())
                    phase = 8
                    panel._apply(None)
                    wx.CallLater(150, check)
                    return
                if phase == 8:
                    if panel._busy:
                        wx.CallLater(100, check)
                        return
                    dru = (std_board.with_suffix('.kicad_dru')).read_text()
                    assert '# kicad_project_manager: begin company rules' in dru
                    assert '# existing exception stays untouched' in dru
                    assert 'Test domain' in dru
                    project_text = std_project.read_text()
                    assert '"unrelated": "keep"' in project_text
                    assert '"min_track_width": 0.1' in project_text
                    assert '"min_clearance": 0.2' in project_text
                    assert '"name": "Default"' in project_text
                    assert '"name": "SIGNAL"' in project_text
                    assert std_board.read_bytes() == before[std_board]
                    assert (std_project.parent / 'kpm-standards.json').is_file()
                    phase = 9
                    frame.Raise()
                    wx.CallLater(600, check)
                    return
                # Capture only this test window's client area, never the user's desktop.
                frame.Update()
                size = frame.GetClientSize()
                bitmap = wx.Bitmap(size.width, size.height)
                memory = wx.MemoryDC(bitmap)
                memory.Blit(0, 0, size.width, size.height, wx.ClientDC(frame), 0, 0)
                memory.SelectObject(wx.NullBitmap)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                if not bitmap.SaveFile(str(args.output), wx.BITMAP_TYPE_PNG):
                    raise AssertionError("Could not write GUI preview")
                frame.Close()
                return
            wx.CallLater(150, check)
        except Exception as exc:
            failures.append(str(exc) or type(exc).__name__)
            frame.Destroy()

    wx.CallLater(250, check)
    app.MainLoop()
    temporary.cleanup()
    if failures:
        raise SystemExit("GUI smoke failed: " + "; ".join(failures))
    print("GUI connected, recovered, cancelled preview, applied with preview, applied directly without dialogs, verified backups, saved standards settings, compared and applied presets, and closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
