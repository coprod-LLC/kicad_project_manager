"""Exercise native GUI refresh/error/close behavior using synthetic project data."""

from __future__ import annotations

import argparse
import faulthandler
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
from project_manager.template_ui import (MATCH_COLOUR, MODIFIED_COLOUR, TemplateDetailsDialog,
                                        TemplateReviewDialog, item_colour)
from project_manager.templates import STATE, compare, load_template
from project_manager.preferences import load_standards
from project_manager.settings_ui import StandardsFolderDialog
from tests.fixtures import sample_project
from tests.test_templates import template_folder
from tests.standards_fixtures import RULES, detailed_catalog, saved_board


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".dev/gui.png"))
    args = parser.parse_args()
    temporary = TemporaryDirectory()
    base = Path(temporary.name)
    project = sample_project(base / 'project', workflow=False)
    template = template_folder(base / 'template').root
    sample = ProjectContext(
        kicad_version="9.0.9", project_name="sample",
        project_directory=str(project),
        project_file=str(project / 'sample.kicad_pro'),
        board_file=str(project / 'sample.kicad_pcb'),
        project_saved=True, board_saved=True, kibot_config_present=True,
    )
    standards = detailed_catalog(base / 'standards')
    std_project, std_board = saved_board(base / 'standards_project')
    std_board.with_suffix('.kicad_dru').write_text(
        RULES.replace('0.2mm', '0.15mm') + '# existing exception stays untouched\n'
        '(rule "Local exception" (constraint courtyard_clearance (min -1mm)))\n')
    standards_context = ProjectContext(
        kicad_version="9.0.9", project_name="sample", project_directory=str(std_project.parent),
        project_file=str(std_project), board_file=str(std_board),
        project_saved=True, board_saved=True, kibot_config_present=False,
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
    logged_phase = None
    source_script = template / 'kibot_resources/scripts/helper.py'
    source_script_before = source_script.read_bytes()
    small_status_height = 0

    def capture(window, path):
        window.Update()
        size = window.GetClientSize()
        bitmap = wx.Bitmap(size.width, size.height)
        memory = wx.MemoryDC(bitmap)
        memory.Blit(0, 0, size.width, size.height, wx.ClientDC(window), 0, 0)
        memory.SelectObject(wx.NullBitmap)
        path.parent.mkdir(parents=True, exist_ok=True)
        assert bitmap.SaveFile(str(path), wx.BITMAP_TYPE_PNG)

    def check_result_colours():
        print('GUI: checking Details colors', flush=True)
        coloured_project = sample_project(base / 'colour-project')
        config = coloured_project / 'kibot_yaml/kibot_main.yaml'
        config.write_bytes(config.read_bytes().replace(b'UNRELATED:', b'PROJECT_OVERRIDE:'))
        comparison = compare(coloured_project, load_template(template))
        expected = {item.path: (MISSING_COLOUR if item.current is None else
                               MATCH_COLOUR if item.status == 'Matches' else MODIFIED_COLOUR)
                    for item in comparison.items}
        assert set(colour.Get() for colour in expected.values()) == {
            MATCH_COLOUR.Get(), MODIFIED_COLOUR.Get(), MISSING_COLOUR.Get()}
        with TemplateDetailsDialog(frame, comparison) as dialog:
            def inspect_details():
                try:
                    contents = dialog.results.GetValue()
                    for item in comparison.items:
                        offset = contents.index(f'{item.status}: {item.path}')
                        style = wx.TextAttr()
                        assert dialog.results.GetStyle(offset, style), item.path
                        assert style.GetTextColour() == expected[item.path], item.path
                    capture(dialog, args.output.with_name('template-details-colours.png'))
                except Exception as exc:
                    failures.append(f'Details colors: {exc}')
                finally:
                    dialog.EndModal(wx.ID_OK)
            wx.CallLater(200, inspect_details)
            dialog.ShowModal()
        assert not failures
        with TemplateReviewDialog(frame, comparison) as dialog:
            for index, item in enumerate(comparison.items):
                assert item_colour(item) == expected[item.path]
                assert dialog.files.GetItemTextColour(index) == expected[item.path]

    def review_template():
        print('GUI: cancelling template review', flush=True)
        # Cancel first; then add missing files through the only template review path.
        def cancel_review():
            dialogs = [w for w in wx.GetTopLevelWindows() if isinstance(w, TemplateReviewDialog) and w.IsModal()]
            assert len(dialogs) == 1
            dialogs[0].EndModal(wx.ID_CANCEL)
        wx.CallLater(150, cancel_review)
        frame._review_template(None)
        assert not (project / STATE).exists()
        assert read_setup(project).workflow is None

        def accept_apply():
            dialogs = [w for w in wx.GetTopLevelWindows() if isinstance(w, PreviewDialog) and w.IsModal()]
            assert len(dialogs) == 1
            dialogs[0].EndModal(wx.ID_OK)

        def accept_review():
            dialogs = [w for w in wx.GetTopLevelWindows() if isinstance(w, TemplateReviewDialog) and w.IsModal()]
            assert len(dialogs) == 1
            dialog = dialogs[0]
            index = next(i for i, item in enumerate(dialog.comparison.items)
                         if item.path.endswith('/assembly_notes.txt'))
            dialog.files.Select(index)
            dialog._load(index)
            dialog.editor.SetValue('Project-specific assembly notes\n')
            assert dialog.choices[dialog.comparison.items[index].path] == 'edit'
            capture(dialog, args.output.with_name('template-review.png'))
            wx.CallLater(150, accept_apply)
            event = wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK)
            dialog.GetEventHandler().ProcessEvent(event)

        wx.CallLater(150, accept_review)
        print('GUI: applying template review', flush=True)
        frame._review_template(None)

    def assert_template_layout():
        label = frame.template_label.GetScreenRect()
        path = frame.template_path.GetScreenRect()
        assert abs((label.y + label.height / 2) - (path.y + path.height / 2)) <= 2
        status = frame.template_status.GetScreenRect()
        buttons = frame.template_details_button.GetScreenRect()
        assert status.y + status.height <= buttons.y, 'Template status overlaps buttons'
        for line in frame.template_status.GetLabel().splitlines():
            assert frame.template_status.GetTextExtent(line).width <= status.width, 'Template status clips horizontally'

    def assert_header_layout():
        refresh = frame.refresh_button.GetScreenRect()
        page = frame.notebook.GetCurrentPage().GetScreenRect()
        assert refresh.bottom <= page.top, f'Refresh overlaps the page content: {refresh}, page {page}'
        assert refresh.right <= frame.notebook.GetScreenRect().right
        assert refresh.left > frame.notebook.GetScreenRect().left + 450, 'Refresh crowds the tabs'

    def check():
        nonlocal phase, before, small_status_height, logged_phase
        try:
            if time.monotonic() - started > 30:
                raise AssertionError("GUI refresh timed out")
            if frame._busy or frame._overview_layout_pending or not calls:
                wx.CallLater(100, check)
                return
            if logged_phase != phase:
                print(f'GUI: phase {phase}', flush=True)
                logged_phase = phase
            if phase == 0:
                assert frame.fields["project_name"].GetValue() == "sample"
                assert frame.notebook.GetSelection() == 0
                assert not hasattr(frame, 'status')
                assert not hasattr(frame, 'settings_button')
                assert not hasattr(frame.stackup_panel, 'reload_button')
                assert_header_layout()
                assert frame.backups_path.GetValue() == str(base / 'settings' / 'backups')
                assert not frame.backups_button.IsEnabled()
                assert 'workflow' not in frame.fields
                assert frame.template_path.GetValue() == 'No template selected'
                assert not hasattr(frame, 'restore_workflow_button')
                assert frame.setup_panel.snapshot is not None
                assert not frame.setup_panel.status_choice.IsEnabled()
                phase = 1
                event = wx.CommandEvent(wx.EVT_BUTTON.typeId, frame.refresh_button.GetId())
                frame.refresh_button.GetEventHandler().ProcessEvent(event)
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
                panel.fields['REVIEWER'].SetValue('Review Engineer')
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
                    dialogs = [w for w in wx.GetTopLevelWindows() if isinstance(w, PreviewDialog) and w.IsModal()]
                    assert len(dialogs) == 1
                    dialogs[0].EndModal(wx.ID_OK)
                wx.CallLater(150, accept_preview)
                panel._preview(None)
                phase = 3
            elif phase == 3:
                saved = read_setup(project)
                assert saved.metadata['DESIGNER'] == 'Alice Martin; Zoë Chen'
                assert saved.metadata['REVIEWER'] == 'Review Engineer'
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
                    frame._choose_template()
                assert seen['path'] == str(template.parent)
                assert 'versioned KiCad template' in seen['message']
                phase = 30
            elif phase == 30:
                assert frame.template_comparison.colour == 'red'
                assert read_setup(project).workflow is None
                check_result_colours()
                review_template()
                phase = 31
            elif phase == 31:
                saved = read_setup(project)
                assert b'kibot_variant: DRAFT  # existing comment' in saved.workflow
                assert (project / STATE).is_file()
                assert frame.template_comparison.colour == 'green'
                assert 'Matches installed template' in frame.template_status.GetLabel()
                assert frame.template_status.GetForegroundColour() == FOUND_COLOUR
                assert (project / 'kibot_resources/fonts/font.ttf').is_file()
                assert (project / 'kibot_resources/templates/assembly_notes.txt').read_text() == 'Project-specific assembly notes\n'
                assert len(list((base / 'settings').rglob('manifest.json'))) == 2
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
                assert frame.backup_path == base / 'settings' / 'backups'
                assert frame.backups_button.IsEnabled()
                with patch('project_manager.ui.wx.LaunchDefaultApplication', return_value=True) as launch:
                    event = wx.CommandEvent(wx.EVT_BUTTON.typeId, frame.backups_button.GetId())
                    frame.backups_button.GetEventHandler().ProcessEvent(event)
                launch.assert_called_once_with(str(frame.backup_path))
                # Save the standards folder through the real OK binding.
                def save_dialog():
                    dialogs = [w for w in wx.GetTopLevelWindows() if isinstance(w, StandardsFolderDialog) and w.IsModal()]
                    assert len(dialogs) == 1
                    dialog = dialogs[0]
                    dialog.folder.SetPath(str(standards))
                    event = wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK)
                    dialog.GetEventHandler().ProcessEvent(event)
                wx.CallLater(150, save_dialog)
                frame._choose_standards()
                assert load_standards(base / 'settings') == str(standards.resolve())
                frame.notebook.SetSelection(2)
                phase = 6
            elif phase == 6:
                panel = frame.rules_panel
                if panel._busy or panel._catalog_busy:
                    wx.CallLater(100, check)
                    return
                assert panel.catalog is not None and panel.catalog.name == 'Example standards'
                assert not panel.compare_button.IsEnabled()
                panel.choices['rules'].SetSelection(1)
                panel._selection_changed()
                assert 'Example rules' in panel.details.GetValue()
                panel.set_context(standards_context)
                assert panel.compare_button.IsEnabled()
                before = {p: p.read_bytes() for p in (std_project, std_board, std_board.with_suffix('.kicad_dru'))}
                panel._compare(None)
                phase = 7
            elif phase in (7, 8):
                panel = frame.rules_panel
                if panel._busy:
                    wx.CallLater(100, check)
                    return
                assert 'Rules: Example rules' in panel.summary.GetValue(), panel.summary.GetValue()
                assert 'Protected' not in panel.summary.GetValue().split('missing from the saved project: ')[-1]
                assert 'min_clearance' in panel.diff.GetValue()
                assert 'company-rules block' in panel.summary.GetValue()
                assert 'Existing shared rules that will change' in panel.summary.GetValue()
                if phase == 7:
                    assert all(p.read_bytes() == data for p, data in before.items())
                    assert panel.rule_values.GetItemCount() == 1
                    assert panel.custom_rules.GetItemCount() == 1
                    capture(frame, args.output.with_name('design-rules.png'))
                    def accept_dependencies():
                        dialogs = [w for w in wx.GetTopLevelWindows() if isinstance(w, PreviewDialog) and w.IsModal()]
                        assert len(dialogs) == 1
                        dialogs[0].EndModal(wx.ID_OK)
                    wx.CallLater(200, accept_dependencies)
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
                    frame.stackup_panel.set_context(standards_context)
                    frame.notebook.SetSelection(3)
                    phase = 80
            elif phase == 80:
                panel = frame.stackup_panel
                if panel._catalog_busy or panel._snapshot_busy:
                    wx.CallLater(100, check)
                    return
                panel.choices['stackups'].SetSelection(2)
                panel._selection_changed()
                assert len(panel.current_stackup.stack.copper) == 2
                assert len(panel.selected_stackup.stack.copper) == 6
                assert any(p.label == 'D3.2' for p in panel.selected_stackup.stack.layers)
                assert 'Example Fab' in panel.selected('stackups').name
                assert str(standards) in frame.standards_path.GetValue()
                for removed in ('compare_button', 'preview_button', 'comparison_pane', 'summary', 'diff', 'capture_button'):
                    assert not hasattr(panel, removed), f'Redundant stackup control: {removed}'
                picker = panel.choices['stackups'].GetScreenRect()
                apply = panel.apply_button.GetScreenRect()
                assert abs((picker.y + picker.height / 2) - (apply.y + apply.height / 2)) <= 2
                assert apply.x > picker.right, 'Apply must follow the preset selector'
                assert panel.stackup_arrow.GetFont().GetPointSize() >= 30
                assert not isinstance(panel.appearance, wx.CollapsiblePane)
                for override in panel.overrides.values():
                    assert override.IsShownOnScreen()
                assert panel.appearance.GetSize().height <= panel.apply_button.GetSize().height + 4
                capture(frame, args.output.with_name('stackup-preview.png'))
                (std_project.parent / '~sample.kicad_pcb.lck').write_text('Synthetic editor lock')
                event = wx.CommandEvent(wx.EVT_BUTTON.typeId, panel.apply_button.GetId())
                panel.apply_button.GetEventHandler().ProcessEvent(event)
                phase = 805
            elif phase == 805:
                panel = frame.stackup_panel
                if panel._busy:
                    wx.CallLater(100, check)
                    return
                assert panel.feedback.IsShown()
                assert 'KiCad still has this project open' in panel.feedback.GetLabel()
                assert panel.feedback.GetForegroundColour() == MODIFIED_COLOUR
                assert std_board.read_bytes() == before[std_board]
                frame.SetSize((860, 640))
                phase = 806
            elif phase == 806:
                panel = frame.stackup_panel
                for line in panel.feedback.GetLabel().splitlines():
                    assert panel.feedback.GetTextExtent(line).width <= panel.feedback.GetSize().width
                assert panel.feedback.GetScreenRect().bottom <= panel.GetScreenRect().bottom
                capture(frame, args.output.with_name('stackup-blocked-small.png'))
                (std_project.parent / '~sample.kicad_pcb.lck').unlink()
                frame.SetSize((1100, 940))
                panel._apply(None)
                phase = 81
            elif phase == 81:
                panel = frame.stackup_panel
                if panel._busy or panel._snapshot_busy:
                    wx.CallLater(100, check)
                    return
                assert len(panel.current_stackup.stack.copper) == 6
                assert len(panel.selected_stackup.stack.copper) == 6
                assert panel.current_stackup.stack.impedance_controlled
                assert 'Stackup applied' in panel.feedback.GetLabel()
                assert panel.feedback.GetForegroundColour() == MATCH_COLOUR
                assert 'Example Fab · EX0616' in panel.current_stackup.title.GetLabel()
                assert 'settings differ' not in panel.current_stackup.title.GetLabel()
                assert panel.current_stackup.details_button.GetScreenRect().right <= panel.current_stackup.GetScreenRect().right
                assert_header_layout()
                capture(frame, args.output.with_name('stackup-applied.png'))
                before = {p: p.read_bytes() for p in (std_project, std_board, std_board.with_suffix('.kicad_dru'))}
                panel._apply(None)
                phase = 82
            elif phase == 82:
                panel = frame.stackup_panel
                if panel._busy or panel._snapshot_busy:
                    wx.CallLater(100, check)
                    return
                assert 'already applied' in panel.feedback.GetLabel()
                assert panel.feedback.GetForegroundColour() == MATCH_COLOUR
                assert all(p.read_bytes() == data for p, data in before.items())
                assert len(list((base / 'settings').rglob('manifest.json'))) == 6
                phase = 9
                frame.notebook.SetSelection(0)
                frame.SetSize((860, 640))
                source_script.write_bytes(source_script_before + b'# edited installed template\n')
                frame.refresh(discard=True)
                frame.Raise()
            elif phase == 9:
                assert frame.template_comparison is None
                assert 'Installed template modified' in frame._template_status_text
                assert 'manifest' not in frame.template_status.GetLabel().lower()
                assert frame.template_status.GetForegroundColour() == MISSING_COLOUR
                assert frame.template_details_button.IsEnabled()
                assert not frame.template_review_button.IsEnabled()
                with patch('project_manager.ui.show_text_details') as details:
                    frame._template_details(None)
                assert 'kibot_resources/scripts/helper.py' in details.call_args.args[1]
                assert_template_layout()
                assert_header_layout()
                small_status_height = frame.template_status.GetSize().height
                capture(frame, args.output.with_name('template-modified-small.png'))
                phase = 10
                frame.SetSize((1200, 840))
            elif phase == 10:
                assert_template_layout()
                assert_header_layout()
                assert frame.template_status.GetSize().height <= small_status_height
                phase = 11
                frame.SetSize((860, 640))
            elif phase == 11:
                assert_template_layout()
                assert frame.template_status.GetSize().height == small_status_height
                source_script.write_bytes(source_script_before)
                frame.SetSize((980, 840))
                frame.refresh(discard=True)
                phase = 12
            else:
                assert frame.template_comparison.colour == 'green'
                assert_template_layout()
                capture(frame, args.output)
                frame.Close()
                return
            wx.CallLater(150, check)
        except Exception as exc:
            failures.append(str(exc) or type(exc).__name__)
            frame.Destroy()

    wx.CallLater(250, check)
    # The phase timeout cannot fire while a failed callback leaves a modal dialog
    # open. Bound the entire event loop and print thread stacks on a stuck test.
    faulthandler.dump_traceback_later(45, exit=True)
    app.MainLoop()
    faulthandler.cancel_dump_traceback_later()
    temporary.cleanup()
    if failures:
        raise SystemExit("GUI smoke failed: " + "; ".join(failures))
    print("GUI opens on Overview, adds the workflow through template review, preserves setup and standards, "
          "and shows readable template errors with Details at narrow and wide window sizes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
