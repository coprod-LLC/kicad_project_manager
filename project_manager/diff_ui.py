"""Shared native coloring for reference comparisons and planned file changes."""
from __future__ import annotations

import wx

DIFF_ADDED_COLOUR = wx.Colour(35, 145, 65)
DIFF_REMOVED_COLOUR = wx.Colour(200, 60, 60)
DIFF_HEADER_COLOUR = wx.Colour(65, 125, 195)


def show_diff(control: wx.TextCtrl, value: str):
    control.Freeze()
    try:
        neutral = control.GetForegroundColour()
        control.ChangeValue('')
        for line in value.splitlines(True):
            colour = (DIFF_HEADER_COLOUR if line.startswith(('---', '+++', '@@')) else
                      DIFF_ADDED_COLOUR if line.startswith('+') else
                      DIFF_REMOVED_COLOUR if line.startswith('-') else neutral)
            control.SetDefaultStyle(wx.TextAttr(colour))
            control.AppendText(line)
        control.SetDefaultStyle(wx.TextAttr(neutral))
        control.ShowPosition(0)
    finally:
        control.Thaw()
