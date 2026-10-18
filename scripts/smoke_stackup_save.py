"""Exercise the native Save as preset flow on temporary synthetic files."""
from __future__ import annotations

import argparse
import faulthandler
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import traceback

if os.environ.get('DISPLAY'):
    os.environ.setdefault('GDK_BACKEND', 'x11')

import wx

from project_manager.context import ProjectContext
from project_manager.preferences import save_settings
from project_manager.stackup_save_ui import ImpedanceTargetDialog, SaveStackupDialog
from project_manager.standards import load_catalog
from project_manager.standards_ui import StandardsPanel
from tests.standards_fixtures import detailed_catalog, saved_board


def click(window):
    event = wx.CommandEvent(wx.EVT_BUTTON.typeId, window.GetId())
    window.GetEventHandler().ProcessEvent(event)


def capture(window, path):
    window.Update()
    size = window.GetClientSize()
    bitmap = wx.Bitmap(size.width, size.height)
    dc = wx.MemoryDC(bitmap)
    dc.Blit(0, 0, size.width, size.height, wx.ClientDC(window), 0, 0)
    dc.SelectObject(wx.NullBitmap)
    path.parent.mkdir(parents=True, exist_ok=True)
    bitmap.SaveFile(str(path), wx.BITMAP_TYPE_PNG)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.dev/save-preset'))
    args = parser.parse_args()
    app = wx.App(False)
    with TemporaryDirectory() as folder:
        root = Path(folder)
        catalog_root = detailed_catalog(root / 'standards')
        project, board = saved_board(root / 'project')
        board.write_text(board.read_text().replace('(thickness 1.53)', '(thickness 1.2)'))
        (project.parent / '~sample.kicad_pcb.lck').write_text('Editor can remain open during capture')
        settings = root / 'settings'
        save_settings({'standards_directory': str(catalog_root)}, settings)
        before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
        frame = wx.Frame(None, title='Save as preset verification', size=(1000, 760))
        panel = StandardsPanel(frame, settings, kind='stackups')
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(panel, 1, wx.EXPAND)
        frame.SetSizer(layout)
        panel.set_context(ProjectContext('9.0.9', 'sample', str(project.parent), str(project), str(board), True, True, False))
        panel.reload_catalog()
        frame.Show()
        phase, failures, saved_id = 0, [], ''
        def dialog_of(kind):
            return next((w for w in wx.GetTopLevelWindows() if isinstance(w, kind) and w.IsModal()), None)
        def check():
            nonlocal phase, saved_id
            try:
                dialog = dialog_of(SaveStackupDialog)
                if phase == 0:
                    if panel._snapshot_busy or panel._catalog_busy:
                        wx.CallLater(100, check)
                        return
                    assert panel.capture_button.IsEnabled()
                    assert panel.capture_button.IsShownOnScreen()
                    save_rect = panel.capture_button.GetScreenRect()
                    details_rect = panel.current_stackup.details_button.GetScreenRect()
                    assert save_rect.y >= details_rect.bottom
                    assert save_rect.x == details_rect.x and save_rect.right == details_rect.right
                    assert save_rect.bottom <= panel.current_stackup.drawing.GetScreenRect().y
                    assert panel.current_stackup.drawing.GetScreenRect().y == panel.selected_stackup.drawing.GetScreenRect().y
                    assert panel.current_stackup.title.GetScreenRect().right <= save_rect.x
                    phase = 1
                    click(panel.capture_button)
                elif phase == 1:
                    if not dialog:
                        wx.CallLater(100, check)
                        return
                    assert not dialog.save_button.IsEnabled()
                    for key, value in {'manufacturer': 'Example Fab', 'name': 'Saved construction',
                                       'manufacturer_stackup_id': 'EX-NEW', 'nominal_thickness_mm': '1.6',
                                       'source_url': 'https://example.org/stackup'}.items():
                        dialog.fields[key].ChangeValue(value)
                    for index, expected in enumerate((None, True, False)):
                        dialog.recommended.SetSelection(index)
                        assert dialog.metadata()['manufacturer_recommended'] is expected
                    dialog._check()
                    assert dialog.save_button.IsEnabled()
                    assert dialog.plan.matches == ()
                    dialog.pages.SetSelection(1)
                    phase = 2
                    wx.CallLater(100, check)
                    dialog._add_guide(None)
                    return
                elif phase == 2:
                    guide = dialog_of(ImpedanceTargetDialog)
                    if not guide:
                        wx.CallLater(100, check)
                        return
                    for key, value in {'impedance_ohms': '100', 'track_width_mm': '0.2', 'pair_gap_mm': '0.15'}.items():
                        guide.fields[key].ChangeValue(value)
                    phase = 21
                elif phase == 21:
                    guide = dialog_of(ImpedanceTargetDialog)
                    assert guide
                    capture(guide, args.output / 'impedance-guide.png')
                    phase = 3
                    click(guide.FindWindow(wx.ID_OK))
                elif phase == 3:
                    assert dialog and len(dialog.targets) == 1
                    assert dialog.guides.GetItemCount() == 1
                    capture(dialog, args.output / 'impedance-list.png')
                    dialog.pages.SetSelection(0)
                    dialog.SetSize((720, 580))
                    phase = 31
                elif phase == 31:
                    assert dialog.save_button.GetScreenRect().bottom <= dialog.GetScreenRect().bottom
                    assert dialog.save_button.IsEnabled()
                    metadata_page = dialog.pages.GetPage(0)
                    assert metadata_page.GetVirtualSize().height > metadata_page.GetClientSize().height
                    capture(dialog, args.output / 'preset-details-small.png')
                    dialog.SetSize((870, 740))
                    phase = 32
                elif phase == 32:
                    capture(dialog, args.output / 'preset-details.png')
                    dialog.pages.GetPage(0).Scroll(0, 100)
                    saved_id = dialog.plan.preset_id
                    phase = 33
                elif phase == 33:
                    assert dialog.fields['assumptions'].GetScreenRect().bottom <= dialog.pages.GetPage(0).GetScreenRect().bottom
                    capture(dialog, args.output / 'preset-details-bottom.png')
                    phase = 4
                    click(dialog.save_button)
                elif phase == 4:
                    if dialog or panel._busy or panel._snapshot_busy:
                        wx.CallLater(100, check)
                        return
                    assert len(panel.catalog.stackups) == 3
                    assert panel.selected('stackups').id == saved_id
                    assert 'Preset saved' in panel.feedback.GetLabel()
                    after = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
                    added = after.keys() - before.keys()
                    assert len(added) == 1
                    assert all(after[path] == contents for path, contents in before.items())
                    document = json.loads(next(iter(added)).read_text())
                    assert 'status' not in document
                    assert document['manufacturer_recommended'] is False
                    assert document['impedance_targets'][0]['pair_gap_mm'] == 0.15
                    assert 'No' in panel.details.GetValue() or 'alternative' in panel.details.GetValue()
                    assert not panel.capture_button.IsShown()
                    capture(frame, args.output / 'matched-stackup.png')
                    # Surface settings do not make a construction a new preset.
                    board.write_text(board.read_text().replace('(copper_finish "ENIG")', '(copper_finish "HASL")'))
                    panel.set_context(panel.context)
                    phase = 41
                elif phase == 41:
                    if panel._snapshot_busy:
                        wx.CallLater(100, check)
                        return
                    assert panel.current_stackup.stack.finish == 'HASL'
                    assert not panel.capture_button.IsShown()
                    assert 'settings differ' in panel.current_stackup.title.GetLabel()
                    # A distinct construction can still be checked for duplicate
                    # manufacturer/code and for similar catalog entries.
                    board.write_text(board.read_text().replace('(thickness 1.2)', '(thickness 1.205)'))
                    panel.set_context(panel.context)
                    phase = 42
                elif phase == 42:
                    if panel._snapshot_busy:
                        wx.CallLater(100, check)
                        return
                    assert panel.capture_button.IsShownOnScreen()
                    assert panel.capture_button.IsEnabled()
                    capture(frame, args.output / 'unmatched-stackup.png')
                    phase = 5
                    click(panel.capture_button)
                elif phase == 5:
                    if not dialog:
                        wx.CallLater(100, check)
                        return
                    for key, value in {'manufacturer': 'Example Fab', 'name': 'Duplicate',
                                       'manufacturer_stackup_id': 'EX-NEW'}.items():
                        dialog.fields[key].ChangeValue(value)
                    dialog._check()
                    assert any(match.blocking for match in dialog.plan.matches)
                    assert not dialog.save_button.IsEnabled()
                    dialog.pages.SetSelection(3)
                    phase = 51
                elif phase == 51:
                    capture(dialog, args.output / 'duplicate.png')
                    assert dialog.use_existing.IsEnabled()
                    phase = 6
                    click(dialog.use_existing)
                elif phase == 6:
                    if dialog or panel._busy or panel._snapshot_busy:
                        wx.CallLater(100, check)
                        return
                    assert panel.selected('stackups').id == saved_id
                    assert len(load_catalog(catalog_root).stackups) == 3
                    phase = 7
                    click(panel.capture_button)
                elif phase == 7:
                    if not dialog:
                        wx.CallLater(100, check)
                        return
                    dialog.fields['manufacturer'].ChangeValue('Another Fab')
                    dialog.fields['name'].ChangeValue('Another supplier')
                    dialog._check()
                    assert dialog.plan.matches and not any(m.blocking for m in dialog.plan.matches)
                    assert not dialog.save_button.IsEnabled()
                    dialog.pages.SetSelection(3)
                    dialog.acknowledge.SetValue(True)
                    dialog._update_save_button()
                    assert dialog.save_button.IsEnabled()
                    phase = 71
                elif phase == 71:
                    capture(dialog, args.output / 'similar.png')
                    board.write_text(board.read_text() + '\n')
                    phase = 8
                    click(dialog.save_button)
                elif phase == 8:
                    if dialog and dialog._saving:
                        wx.CallLater(100, check)
                        return
                    assert dialog and 'board changed' in dialog.status.GetLabel()
                    assert len(load_catalog(catalog_root).stackups) == 3
                    capture(dialog, args.output / 'stale-board.png')
                    phase = 9
                    click(dialog.FindWindow(wx.ID_CANCEL))
                else:
                    if dialog or panel._busy:
                        wx.CallLater(100, check)
                        return
                    assert len(load_catalog(catalog_root).stackups) == 3
                    frame.Close()
                    return
                wx.CallLater(120, check)
            except Exception as exc:
                traceback.print_exc()
                failures.append(str(exc) or type(exc).__name__)
                for window in wx.GetTopLevelWindows():
                    if isinstance(window, wx.Dialog) and window.IsModal():
                        window.EndModal(wx.ID_CANCEL)
                frame.Destroy()
        wx.CallLater(150, check)
        faulthandler.dump_traceback_later(45, exit=True)
        app.MainLoop()
        faulthandler.cancel_dump_traceback_later()
        if failures:
            raise SystemExit('Save as preset GUI failed: ' + '; '.join(failures))
    print('Save as preset GUI passed: metadata, impedance, single-file save, duplicate selection, similarity review, stale-board refusal and cancellation.')


if __name__ == '__main__':
    main()
