"""Exercise native GUI refresh/error/close behavior using synthetic project data."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory

# GTK/Wayland does not support ClientDC readback. This affects screenshots only.
if sys.platform == "linux" and os.environ.get("DISPLAY"):
    os.environ.setdefault("GDK_BACKEND", "x11")

import wx

from project_manager.context import ContextError, ProjectContext
from project_manager.ui import ProjectManagerFrame
from project_manager.setup import read_setup
from project_manager.setup_ui import PreviewDialog
from tests.fixtures import sample_project


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
        nonlocal phase
        try:
            if time.monotonic() - started > 25:
                raise AssertionError("GUI refresh timed out")
            if frame._busy or not calls:
                wx.CallLater(100, check)
                return
            if phase == 0:
                assert frame.fields["project_name"].GetValue() == "sample"
                assert frame.fields["workflow"].GetValue() == "Not found"
                assert frame.setup_panel.snapshot is not None
                phase = 1
                frame.refresh()
            elif phase == 1:
                assert frame._context is None
                assert frame.fields["board_file"].GetValue() == "—"
                assert "Test connection interruption" in frame.log.GetValue()
                assert not frame.setup_panel.preview_button.IsEnabled()
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
                panel.template.SetPath(str(template))
                panel.status_choice.SetStringSelection('PRELIMINARY')
                panel.update_status_help()
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
            else:
                saved = read_setup(project)
                assert saved.metadata['DESIGNER'] == 'Alice Martin; Zoë Chen'
                assert saved.status == 'PRELIMINARY'
                assert not frame.setup_panel.dirty
                assert not frame.setup_panel.make_plan().changes
                assert not frame.setup_panel.restore.IsEnabled()
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
    print("GUI connected, recovered, cancelled a preview, applied setup, reloaded, captured preview, and closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
