"""Capture the documented native interface using disposable synthetic examples."""
from __future__ import annotations

import argparse
import faulthandler
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import traceback

if os.environ.get('DISPLAY'):
    os.environ.setdefault('GDK_BACKEND', 'x11')

import wx

from project_manager.context import ProjectContext
from project_manager.preferences import save_settings, save_template
from project_manager.setup import apply, prepare, read_setup
from project_manager.template_ui import TemplateReviewDialog
from project_manager.templates import apply_upgrade, compare, prepare_upgrade
from project_manager.ui import ProjectManagerFrame
from tests.standards_fixtures import detailed_catalog, saved_board
from tests.test_templates import release, template_folder


def capture(window, path):
    window.Update()
    size = window.GetClientSize()
    bitmap = wx.Bitmap(size.width, size.height)
    dc = wx.MemoryDC(bitmap)
    dc.Blit(0, 0, size.width, size.height, wx.ClientDC(window), 0, 0)
    dc.SelectObject(wx.NullBitmap)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not bitmap.SaveFile(str(path), wx.BITMAP_TYPE_PNG):
        raise RuntimeError(f'Could not save {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('docs/screenshots'))
    args = parser.parse_args()
    app = wx.App(False)
    with TemporaryDirectory(prefix='kicad-project-manager-', dir='/tmp' if os.name == 'posix' else None) as folder:
        root = Path(folder)
        template = template_folder(root / 'template')
        project = root / 'controller'
        shutil.copytree(template.root, project)
        project_file, board = saved_board(project)
        storage = root / 'settings'
        snapshot = read_setup(project)
        metadata = {**snapshot.metadata, 'PROJECT_NAME': 'Sensor controller', 'BOARD_NAME': 'Control board',
                    'COMPANY': 'Example team', 'DESIGNER': 'Alice Martin; Zoë Chen',
                    'REVIEWER': 'Sam Lee', 'GIT_URL': 'https://example.org/sensor-controller'}
        apply(prepare(snapshot, metadata, 'PRELIMINARY'), storage)
        comparison = compare(project, template)
        apply_upgrade(prepare_upgrade(comparison, {item.path: item.suggested for item in comparison.items}), storage)
        (template.root / 'kibot_resources/scripts/helper.py').write_text('print("Updated automation")\n')
        template = release(template.root, '1.1.0')
        standards = detailed_catalog(root / 'standards')
        save_settings({'standards_directory': str(standards)}, storage)
        save_template(str(template.root), storage, template.identity)
        context = ProjectContext('9.0.9', 'Sensor controller', str(project), str(project_file),
                                 str(board), True, True, True)
        frame = ProjectManagerFrame(lambda: context, storage)
        frame.SetSize((1100, 900))
        frame.Show()
        phase, failures = 0, []

        def check():
            nonlocal phase
            try:
                if (frame._busy or frame._overview_layout_pending
                        or any(panel._busy or panel._catalog_busy or panel._snapshot_busy
                               for panel in frame.standards_panels)):
                    wx.CallLater(100, check)
                    return
                if phase == 0:
                    capture(frame, args.output / 'overview.png')
                    frame.notebook.SetSelection(1)
                elif phase == 1:
                    capture(frame, args.output / 'project-setup.png')
                    frame.notebook.SetSelection(2)
                    frame.rules_panel.choices['rules'].SetSelection(1)
                    frame.rules_panel._selection_changed()
                    frame.rules_panel._compare(None)
                elif phase == 2:
                    capture(frame, args.output / 'design-rules.png')
                    frame.notebook.SetSelection(3)
                    panel = frame.stackup_panel
                    index = next(i for i, preset in enumerate(panel.catalog.stackups, 1)
                                 if preset.id == 'example_six_layer')
                    panel.choices['stackups'].SetSelection(index)
                    panel._selection_changed()
                else:
                    capture(frame, args.output / 'stackup.png')
                    with TemplateReviewDialog(frame, compare(project, template)) as dialog:
                        dialog.files.Select(0, False)
                        for index, item in enumerate(dialog.comparison.items):
                            if item.path.endswith(('/helper.py', '/assembly_notes.txt')):
                                dialog.files.Select(index)
                        dialog._refresh_selection()
                        def capture_review():
                            try:
                                capture(dialog, args.output / 'template-review.png')
                            except Exception as exc:
                                failures.append(str(exc))
                            finally:
                                dialog.EndModal(wx.ID_CANCEL)
                        wx.CallLater(150, capture_review)
                        dialog.ShowModal()
                    frame.Close()
                    return
                phase += 1
                wx.CallLater(150, check)
            except Exception as exc:
                traceback.print_exc()
                failures.append(str(exc))
                frame.Destroy()

        wx.CallLater(250, check)
        faulthandler.dump_traceback_later(30, exit=True)
        app.MainLoop()
        faulthandler.cancel_dump_traceback_later()
        if failures:
            raise SystemExit('; '.join(failures))
    print(f'Captured four main pages and template review in {args.output}')


if __name__ == '__main__':
    main()
