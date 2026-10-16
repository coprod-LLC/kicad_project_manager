"""Exercise native GUI refresh/error/close behavior using synthetic project data."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

# GTK/Wayland does not support ClientDC readback. This affects screenshots only.
if sys.platform == "linux" and os.environ.get("DISPLAY"):
    os.environ.setdefault("GDK_BACKEND", "x11")

import wx

from project_manager.context import ContextError, ProjectContext
from project_manager.ui import ProjectManagerFrame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".dev/gui.png"))
    args = parser.parse_args()
    sample = ProjectContext(
        kicad_version="9.0.9", project_name="example_board",
        project_directory="/example/example_board",
        project_file="/example/example_board/example_board.kicad_pro",
        board_file="/example/example_board/example_board.kicad_pcb",
        project_saved=True, board_saved=True, kibot_config_present=True, workflow_present=False,
    )
    calls = []

    def provider():
        calls.append(1)
        if len(calls) == 2:
            raise ContextError("Test connection interruption. Click Refresh to reconnect.")
        return sample

    app = wx.App(False)
    frame = ProjectManagerFrame(provider)
    frame.Show()
    started = time.monotonic()
    failures = []
    phase = 0

    def check():
        nonlocal phase
        try:
            if time.monotonic() - started > 15:
                raise AssertionError("GUI refresh timed out")
            if frame._busy or not calls:
                wx.CallLater(100, check)
                return
            if phase == 0:
                assert frame.fields["project_name"].GetValue() == "example_board"
                assert frame.fields["workflow"].GetValue() == "Not found"
                phase = 1
                frame.refresh()
            elif phase == 1:
                assert frame._context is None
                assert frame.fields["board_file"].GetValue() == "—"
                assert "Test connection interruption" in frame.log.GetValue()
                phase = 2
                frame.refresh()
            else:
                assert frame._context == sample
                assert frame.refresh_button.IsEnabled()
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
            frame.Close()

    wx.CallLater(250, check)
    app.MainLoop()
    if failures:
        raise SystemExit("GUI smoke failed: " + "; ".join(failures))
    print("GUI connected, recovered after an error, captured preview, and closed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
