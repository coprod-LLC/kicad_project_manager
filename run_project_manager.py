"""KiCad IPC entrypoint; also provides a read-only terminal diagnostic."""

from __future__ import annotations

import argparse
import json
import sys

from project_manager import NAME, __version__
from project_manager.context import ContextError, detect_context


def main() -> int:
    parser = argparse.ArgumentParser(description=NAME)
    parser.add_argument("--version", action="version", version=f"{NAME} {__version__}")
    parser.add_argument("--socket", help="Explicit KiCad IPC endpoint for terminal development")
    parser.add_argument("--diagnose", action="store_true", help="Print project identity as JSON without a GUI")
    args = parser.parse_args()
    provider = lambda: detect_context(args.socket)
    if args.diagnose:
        try:
            print(json.dumps(provider().to_dict(), indent=2))
            return 0
        except ContextError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    try:
        import wx
        from project_manager.ui import ProjectManagerFrame
    except ImportError:
        print(
            "wxPython is unavailable. On Linux install your distro's Python wx package "
            "(python3-wxgtk4.0 on Debian/Ubuntu); on macOS/Windows select KiCad's bundled Python. "
            "Then recreate the plugin environment in Preferences → Plugins.",
            file=sys.stderr,
        )
        return 1
    app = wx.App(False)
    # Bind settings and locks before creating the window. Keep connection errors
    # out of the KiCad 9 settings directory when a 10 launch cannot connect.
    try:
        initial = provider()
    except ContextError as exc:
        wx.MessageBox(str(exc), NAME, wx.OK | wx.ICON_ERROR)
        return 1
    from project_manager.preferences import configure_runtime
    configure_runtime(int(initial.kicad_version.split('.')[0]))
    from project_manager.instance import ensure_single_instance
    if not ensure_single_instance(app):
        return 0
    frame = ProjectManagerFrame(provider)
    frame.Show()
    app.MainLoop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
