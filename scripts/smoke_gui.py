"""Exercise native GUI refresh/error/close behavior using synthetic project data."""

from __future__ import annotations

import argparse
import faulthandler
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from tempfile import TemporaryDirectory
from unittest.mock import patch

# GTK/Wayland does not support ClientDC readback. This affects screenshots only.
if sys.platform == "linux" and os.environ.get("DISPLAY"):
    os.environ.setdefault("GDK_BACKEND", "x11")

import wx

from project_manager.impedance_table import HEADERS, ImpedanceTable
from project_manager.context import ContextError, ProjectContext
from project_manager.ui import FOUND_COLOUR, MISSING_COLOUR, ProjectManagerFrame, _wrap_text
from project_manager.setup import read_setup
from project_manager.setup_ui import PreviewDialog, ProjectSetupPanel
from project_manager.template_ui import (CUSTOM_COLOUR, MATCH_COLOUR, MODIFIED_COLOUR, TemplateDetailsDialog,
                                        TemplateReviewDialog, item_colour, DIFF_ADDED_COLOUR, DIFF_REMOVED_COLOUR)
from project_manager.templates import (IGNORE_SHARED, IGNORE_USER, LICENSE_FILE, README_SOURCE, STATE, apply_upgrade,
                                       compare, load_template, prepare_upgrade)
from project_manager.preferences import load_standards
from project_manager.settings_ui import StandardsFolderDialog
from tests.fixtures import sample_project
from tests.test_templates import release, template_folder
from tests.standards_fixtures import RULES, STACKUP, catalog_folder, detailed_catalog, saved_board, write_stackup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".dev/gui.png"))
    parser.add_argument("--startup-only", action="store_true", help="Show and close the window without exercising later interactions")
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
    if args.startup_only:
        wx.CallLater(500, frame.Close)
        app.MainLoop()
        temporary.cleanup()
        print('GUI startup and close passed', flush=True)
        return 0
    started = time.monotonic()
    failures = []
    phase = 0
    logged_phase = None
    wrap_widths = {}

    def measured_wrap(control, value, width):
        wrapped = _wrap_text(control, value, width)
        wrap_widths[control.GetId()] = (width, max((control.GetTextExtent(line).width
                                                  for line in wrapped.splitlines()), default=0))
        return wrapped

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

    def select_files(dialog, *paths):
        if paths:
            dialog.select_path(paths[0])
        for row in range(dialog.files.GetItemCount()):
            dialog.files.Select(row, False)
        for path in paths:
            index = next(i for i, item in enumerate(dialog.comparison.items) if item.path == path)
            assert index in dialog.visible_indices, path
            dialog.files.Select(dialog.visible_indices.index(index))
        dialog._refresh_selection()

    def choose_action(dialog, value):
        dialog.action.SetSelection(dialog.actions.index(value))
        dialog._action_changed(None)

    def check_result_colours():
        print('GUI: checking Details colors', flush=True)
        coloured_project = sample_project(base / 'colour-project')
        config = coloured_project / 'kibot_yaml/kibot_main.yaml'
        config.write_bytes(config.read_bytes().replace(b'UNRELATED:', b'PROJECT_OVERRIDE:'))
        source = load_template(template)
        document = 'kibot_resources/templates/assembly_notes.txt'
        (coloured_project / document).parent.mkdir(parents=True)
        (coloured_project / document).write_text('Accepted project notes\n')
        apply_upgrade(prepare_upgrade(compare(coloured_project, source), {document: 'keep'}),
                      base / 'colour-backups')
        comparison = compare(coloured_project, load_template(template))
        expected = {item.path: (MISSING_COLOUR if item.current is None else
                               CUSTOM_COLOUR if item.accepted else
                               MATCH_COLOUR if item.status == 'Matches' else MODIFIED_COLOUR)
                    for item in comparison.items}
        assert set(colour.Get() for colour in expected.values()) == {
            MATCH_COLOUR.Get(), CUSTOM_COLOUR.Get(), MODIFIED_COLOUR.Get(), MISSING_COLOUR.Get()}
        with TemplateDetailsDialog(frame, comparison) as dialog:
            def inspect_details(dialog=dialog):
                try:
                    contents = dialog.results.GetValue()
                    capture(dialog, args.output.with_name('template-details-colours.png'))
                    for item in comparison.items:
                        offset = contents.index(f'{item.status}: {item.path}')
                        style = wx.TextAttr()
                        assert dialog.results.GetStyle(offset, style), f'No style at {offset}: {item.path}'
                        actual = style.GetTextColour()
                        # Cocoa can return an NSColor with identical RGBA but
                        # different native colour-space identity.
                        assert actual.Get() == expected[item.path].Get(), (
                            f'{item.path}: actual={actual.Get()}, expected={expected[item.path].Get()}')
                except Exception as exc:
                    failures.append(f'Details colors: {exc}')
                finally:
                    dialog.EndModal(wx.ID_OK)
            inspection = wx.CallLater(200, inspect_details)
            dialog.ShowModal()
            inspection.Stop()
        assert not failures
        with TemplateReviewDialog(frame, comparison) as dialog:
            for category in ('documents', 'template'):
                dialog.show_category(category)
                for row, index in enumerate(dialog.visible_indices):
                    item = comparison.items[index]
                    assert item_colour(item).Get() == expected[item.path].Get()
                    assert dialog.files.GetItemTextColour(row).Get() == expected[item.path].Get()

    def check_document_workspace():
        print('GUI: checking split workspace, drafts, colored diff and protected ignore editor', flush=True)
        source = template_folder(base / 'documents-template')
        (source.root / LICENSE_FILE).write_text('Template licence\n')
        (source.root / '.gitignore').write_text(
            f'{IGNORE_SHARED}\n*.log\n\n{IGNORE_USER}\n# Local rules\n')
        source = release(source.root, '1.1.0')
        root = base / 'documents-project'
        shutil.copytree(source.root, root)
        target = root / README_SOURCE
        target.write_text('My custom board README source\n')
        (root / 'README.md').write_text('Generated README stays unchanged until CI\n')
        with TemplateReviewDialog(frame, compare(root, source),
                                  apply_plan=lambda plan: apply_upgrade(plan, base / 'documents-backups')) as dialog:
            def exercise():
                try:
                    assert dialog.category == 'documents'
                    assert dialog.files.GetItemCount() == 6
                    assert dialog.splitter.IsSplit()
                    assert not hasattr(dialog, 'history') and not hasattr(dialog, 'default_readme_button')
                    dialog.select_path(README_SOURCE)
                    assert dialog.views.GetSelection() == 0 and dialog.editor.IsEnabled()
                    assert dialog.editor.GetClientSize().width > 500
                    dialog.editor.SetValue('My edited board README source\n')
                    dialog.select_path('kibot_resources/scripts/helper.py')
                    assert dialog.category == 'template' and dialog.views.GetSelection() == 1
                    assert not dialog.edit_button.IsShown() and not dialog.editor.IsEnabled()
                    dialog.search.SetValue('helper.py')
                    assert dialog.files.GetItemCount() == 1
                    dialog.search.SetValue('does-not-exist')
                    assert not dialog.action.IsEnabled() and dialog.selected == -1
                    dialog.select_path(README_SOURCE)
                    assert dialog.editor.GetValue() == 'My edited board README source\n'
                    close_event = wx.CloseEvent(wx.EVT_CLOSE.typeId)
                    with patch('project_manager.template_ui.wx.MessageBox', return_value=wx.NO):
                        dialog._close(close_event)
                    assert not close_event.GetSkipped()
                    dialog._show_view(1)
                    value = dialog.diff.GetValue()
                    dialog.diff_mode.SetSelection(1)
                    dialog._diff_mode_changed(None)
                    assert '+My edited board README source' in dialog.diff.GetValue()
                    choose_action(dialog, 'keep')
                    assert 'stay unchanged' in dialog.diff.GetValue()
                    choose_action(dialog, 'edit')
                    dialog.diff_mode.SetSelection(0)
                    dialog._diff_mode_changed(None)
                    assert dialog.diff.GetValue() == value
                    for prefix, colour in (('-My custom', DIFF_REMOVED_COLOUR), ('+Company introduction', DIFF_ADDED_COLOUR)):
                        # Fixtures use their filename as default content.
                        offset = value.index(prefix)
                        style = wx.TextAttr()
                        assert dialog.diff.GetStyle(offset, style)
                        assert style.GetTextColour().Get() == colour.Get()
                    wx.CallLater(150, capture_diff)
                except Exception:
                    traceback.print_exc()
                    failures.append('Document workspace failed')
                    dialog.EndModal(wx.ID_CANCEL)

            def capture_diff():
                try:
                    capture(dialog, args.output.with_name('template-document-diff.png'))
                    dialog._show_view(0)
                    pending = 'kibot_resources/templates/assembly_notes.txt'
                    dialog.select_path(pending)
                    dialog.editor.SetValue('Deferred assembly notes\n')
                    choose_action(dialog, 'review')
                    dialog.select_path('.gitignore')
                    assert dialog.shared_toggle.IsShown()
                    assert '*.log' in dialog.shared_rules.GetValue()
                    assert IGNORE_SHARED not in dialog.editor.GetValue()
                    dialog.editor.SetValue('!keep.log\nprivate/\n')
                    dialog.select_path(README_SOURCE)
                    with patch('project_manager.template_ui.wx.MessageBox', side_effect=AssertionError('Unexpected warning')):
                        dialog._apply(None)
                    assert target.read_text() == 'My edited board README source\n'
                    assert dialog.comparison.colour == 'green'
                    assert len(dialog.comparison.customizations) == 2
                    assert dialog.edits[pending] == b'Deferred assembly notes\n'
                    assert (root / pending).read_bytes() == source.files[pending]
                    assert (root / '.gitignore').read_text().endswith('!keep.log\nprivate/\n')
                    choose_action(dialog, 'use')
                    before = target.read_bytes()
                    with patch('project_manager.template_ui.wx.MessageBox', return_value=wx.NO) as warning:
                        dialog._apply(None)
                        assert 'next CI run will overwrite README.md' in warning.call_args[0][0]
                        assert warning.call_args[0][2] & wx.NO_DEFAULT
                    assert target.read_bytes() == before
                    with patch('project_manager.template_ui.wx.MessageBox', return_value=wx.YES):
                        dialog._apply(None)
                    assert target.read_bytes() == source.files[README_SOURCE]
                    assert dialog.edits[pending] == b'Deferred assembly notes\n'
                    assert (root / 'README.md').read_text() == 'Generated README stays unchanged until CI\n'
                    assert list((base / 'documents-backups').rglob('readme.txt'))
                    # Show the editor with a saved customization and a user draft.
                    dialog.select_path(pending)
                    wx.CallLater(150, capture_editor)
                except Exception:
                    traceback.print_exc()
                    failures.append('Document save / default restore failed')
                    dialog.EndModal(wx.ID_CANCEL)

            def capture_editor():
                try:
                    capture(dialog, args.output.with_name('template-documents-editor.png'))
                    dialog.select_path('.gitignore')
                    dialog.shared_toggle.SetValue(True)
                    dialog._toggle_shared_rules(None)
                    wx.CallLater(150, capture_ignore)
                except Exception:
                    traceback.print_exc()
                    failures.append('Document editor layout failed')
                    dialog.EndModal(wx.ID_CANCEL)

            def capture_ignore():
                try:
                    assert dialog.ignore_splitter.IsSplit()
                    height = dialog.ignore_splitter.GetClientSize().height
                    assert abs(dialog.ignore_splitter.GetSashPosition() - height / 2) < 35
                    capture(dialog, args.output.with_name('template-ignore-editor.png'))
                    dialog.editor.SetValue('# No custom patterns remain\n\n')
                    dialog._apply(None)
                    item = dialog.comparison.items[dialog.selected]
                    assert item.path == '.gitignore' and item.status == 'Matches'
                    row = dialog.visible_indices.index(dialog.selected)
                    assert dialog.files.GetItemTextColour(row).Get() == MATCH_COLOUR.Get()
                    assert not dialog.comparison.customizations
                except Exception:
                    traceback.print_exc()
                    failures.append('Ignore editor layout failed')
                finally:
                    dialog.EndModal(wx.ID_CANCEL)

            wx.CallLater(150, exercise)
            dialog.ShowModal()
            assert not failures

    def check_review_refresh():
        print('GUI: checking safe refresh of external changes and retained drafts', flush=True)
        source = template_folder(base / 'refresh-template')
        root = base / 'refresh-project'
        shutil.copytree(source.root, root)
        script = 'kibot_resources/scripts/helper.py'
        with TemplateReviewDialog(frame, compare(root, source)) as dialog:
            dialog.select_path(README_SOURCE)
            dialog.editor.SetValue('My unsaved README draft\n')
            dialog.select_path(script)
            choose_action(dialog, 'use')
            (source.root / script).write_text('print("new template")\n')
            source = release(source.root, '1.1.0')
            dialog._refresh(None)
            assert dialog.refreshed and dialog.comparison.template.version == '1.1.0'
            assert dialog.comparison.items[dialog.selected].path == script
            assert dialog.choices[script] == 'review'
            assert dialog.edits[README_SOURCE] == b'My unsaved README draft\n'
            dialog.select_path(README_SOURCE)
            (root / README_SOURCE).write_text('External editor saved this README\n')
            previous = dialog.comparison
            with patch('project_manager.template_ui.wx.MessageBox', return_value=wx.NO) as question:
                event = wx.CommandEvent(wx.EVT_BUTTON.typeId, dialog.refresh_button.GetId())
                dialog.refresh_button.GetEventHandler().ProcessEvent(event)
                assert question.call_count == 1 and README_SOURCE in question.call_args[0][0]
            assert dialog.comparison is previous
            assert dialog.edits[README_SOURCE] == b'My unsaved README draft\n'
            with patch('project_manager.template_ui.wx.MessageBox', return_value=wx.YES):
                dialog._refresh(None)
            assert README_SOURCE not in dialog.edits
            assert dialog.editor.GetValue() == 'External editor saved this README\n'
            assert dialog.choices[README_SOURCE] == 'review'

    def check_impedance_editor():
        print('GUI: checking impedance cells, non-destructive imports, raw text and save', flush=True)
        source = template_folder(base / 'impedance-template')
        path = 'kibot_resources/templates/impedance_table.txt'
        (source.root / path).write_text(',\t\t'.join(HEADERS) + '\n')
        source = release(source.root, '1.1.0')
        root = base / 'impedance-project'
        shutil.copytree(source.root, root)
        standards = catalog_folder(base / 'impedance-standards')
        _, board = saved_board(base / 'impedance-board')
        target = {'mode': 'differential', 'impedance_ohms': 100, 'geometry': 'microstrip',
                  'track_width_mm': 0.2146, 'pair_gap_mm': 0.2032, 'layer': 'F.Cu', 'reference_layer': 'B.Cu'}
        write_stackup(standards / 'stackups/two_layer.json', 'two_layer', 'Matching board', STACKUP,
                      impedance_targets=[target])
        with TemplateReviewDialog(frame, compare(root, source),
                                  apply_plan=lambda plan: apply_upgrade(plan, base / 'impedance-backups'),
                                  standards_directory=standards, board_file=board) as dialog:
            def exercise():
                try:
                    dialog.select_path(path)
                    assert dialog.views.GetSelection() == 2
                    assert dialog.source_button.IsShown() and dialog.edit_button.GetLabel() == 'Table'
                    panel = dialog.impedance_panel
                    cell_controls = []
                    def editor_created(event):
                        cell_controls.append(event.GetControl())
                        event.Skip()
                    panel.grid.Bind(wx.grid.EVT_GRID_EDITOR_CREATED, editor_created)
                    assert panel.import_button.IsEnabled() and len(panel.presets) == 1
                    panel._add(None)
                    manual = ('Manual single-ended', '50', '±5 %', 'L1', '0.3', '', 'L2')
                    for col, value in enumerate(manual):
                        panel.grid.SetCellValue(0, col, value)
                    panel._changed(None)
                    panel._import(None)
                    assert panel.grid.GetNumberRows() == 2
                    assert ImpedanceTable.parse(dialog.edits[path].decode()).rows[0] == manual
                    panel._import(None)
                    assert panel.grid.GetNumberRows() == 2
                    dialog.select_path(README_SOURCE)
                    dialog.select_path(path)
                    assert panel.grid.GetCellValue(0, 0) == 'Manual single-ended'
                    dialog._show_view(2)
                    assert '\t' not in dialog.editor.GetValue()
                    dialog._show_view(0)
                    # Make a real in-progress grid edit, then change files.
                    panel.grid.SetGridCursor(0, 4)
                    panel.grid.EnableCellEditControl()
                    assert cell_controls
                    cell_controls[-1].SetValue('0.32')
                    dialog.select_path(README_SOURCE)
                    assert ImpedanceTable.parse(dialog.edits[path].decode()).rows[0][4] == '0.32'
                    dialog.select_path(path)
                    dialog._apply(None)
                    assert len(ImpedanceTable.parse((root / path).read_text()).rows) == 2
                    assert dialog.comparison.colour == 'green'
                    assert next(item for item in dialog.comparison.items if item.path == path).accepted
                    wx.CallLater(150, capture_table)
                except Exception:
                    traceback.print_exc()
                    failures.append('Impedance editor failed')
                    dialog.EndModal(wx.ID_CANCEL)

            def capture_table():
                try:
                    capture(dialog, args.output.with_name('template-impedance-table.png'))
                    # A row deletion is staged and can be viewed as a colored plan.
                    panel = dialog.impedance_panel
                    panel.grid.SetGridCursor(1, 0)
                    panel._delete(None)
                    assert panel.grid.GetNumberRows() == 1
                    dialog._show_view(1)
                    dialog.diff_mode.SetSelection(1)
                    dialog._diff_mode_changed(None)
                    assert '-Differential microstrip' in dialog.diff.GetValue()
                    def cancel_preview():
                        previews = [w for w in wx.GetTopLevelWindows() if isinstance(w, PreviewDialog) and w.IsModal()]
                        assert len(previews) == 1
                        control = previews[0].diff
                        value = control.GetValue()
                        offset = value.index('-Differential microstrip')
                        style = wx.TextAttr()
                        assert control.GetStyle(offset, style)
                        assert style.GetTextColour().Get() == DIFF_REMOVED_COLOUR.Get()
                        capture(previews[0], args.output.with_name('template-colored-preview.png'))
                        previews[0].EndModal(wx.ID_CANCEL)
                    wx.CallLater(150, cancel_preview)
                    dialog._preview(None)
                    assert len(ImpedanceTable.parse((root / path).read_text()).rows) == 2
                except Exception:
                    traceback.print_exc()
                    failures.append('Impedance preview/delete failed')
                finally:
                    dialog.EndModal(wx.ID_CANCEL)

            wx.CallLater(150, exercise)
            dialog.ShowModal()
            assert not failures
        # With no linked standards folder the table is still editable, and
        # importing is absent. Unknown documents keep the ordinary text editor.
        with TemplateReviewDialog(frame, compare(root, source)) as dialog:
            dialog.select_path(path)
            assert not dialog.impedance_panel.import_panel.IsShown()
            assert dialog.views.GetSelection() == 2

    def check_accepted_license():
        print('GUI: checking blue licence choice after reopen, then confirmed restore', flush=True)
        source = template_folder(base / 'license-template')
        (source.root / LICENSE_FILE).write_text('Template licence\n')
        source = release(source.root, '1.1.0')
        root = base / 'license-project'
        shutil.copytree(source.root, root)
        target = root / LICENSE_FILE
        target.write_text('My project licence\n')
        storage = base / 'license-backups'
        with TemplateReviewDialog(frame, compare(root, source),
                                  apply_plan=lambda plan: apply_upgrade(plan, storage)) as dialog:
            dialog.choices[LICENSE_FILE] = 'keep'
            dialog._apply(None)
            assert next(item for item in dialog.comparison.items if item.path == LICENSE_FILE).accepted
        # Create a new dialog from the files on disk, without reusing its choices.
        with TemplateReviewDialog(frame, compare(root, load_template(source.root)),
                                  apply_plan=lambda plan: apply_upgrade(plan, storage)) as dialog:
            dialog.select_path(LICENSE_FILE)
            row = dialog.visible_indices.index(dialog.selected)
            assert dialog.files.GetItemTextColour(row).Get() == CUSTOM_COLOUR.Get()
            assert dialog.choices[LICENSE_FILE] == 'keep'
            assert 'My project licence' in dialog.diff.GetValue()
            assert dialog.comparison.colour == 'green'
            assert '1 accepted customization' in dialog.intro.GetLabel()
            assert 'use' in dialog.actions
            dialog.action.SetSelection(dialog.actions.index('use'))
            dialog._action_changed(None)
            with patch('project_manager.template_ui.wx.MessageBox', return_value=wx.NO) as warning:
                dialog._apply(None)
                assert LICENSE_FILE in warning.call_args[0][0]
            assert target.read_text() == 'My project licence\n'
            with patch('project_manager.template_ui.wx.MessageBox', return_value=wx.YES):
                dialog._apply(None)
            assert target.read_text() == 'Template licence\n'
            assert dialog.files.GetItemTextColour(row).Get() == MATCH_COLOUR.Get()
            assert 'Matches installed template' in dialog.intro.GetLabel()
            assert list(storage.rglob(LICENSE_FILE))

    def check_ci_version_migration():
        print('GUI: checking KiCad 9 to 10 CI migration', flush=True)
        root = sample_project(base / 'ci-migration')
        snapshot = read_setup(root)
        board_before = (root / 'sample.kicad_pcb').read_bytes()
        applied = []
        panel = ProjectSetupPanel(frame.notebook, applied.append, base / 'ci-settings')
        try:
            panel.load(snapshot, snapshot.metadata, editor_major=10)
            assert panel.selected_kicad_major() == 10
            assert not panel.dirty and panel.kicad_choice.IsEnabled()
            assert 'CI currently uses KiCad 9' in panel.message.GetLabel()
            event = wx.CommandEvent(wx.EVT_BUTTON.typeId, panel.apply_button.GetId())
            panel.apply_button.GetEventHandler().ProcessEvent(event)
            saved = read_setup(root)
            assert saved.kicad_major == 10 and len(applied) == 1
            assert (root / 'sample.kicad_pcb').read_bytes() == board_before
            panel.load(saved, saved.metadata, editor_major=9)
            assert panel.selected_kicad_major() == 10 and not panel.dirty
            assert ' '.join(panel.feedback.GetLabel().split()) == 'Changes saved successfully.', repr(panel.feedback.GetLabel())
            panel.fields['PROJECT_NAME'].SetValue('Feedback regression')
            assert not panel.feedback.GetLabel(), 'Editing must clear the previous outcome'
            for failure in (OSError('Read-only project directory'), RuntimeError('Unexpected write failure')):
                with patch('project_manager.setup_ui.apply', side_effect=failure), \
                     patch('project_manager.setup_ui.wx.MessageBox') as message, \
                     patch('project_manager.setup_ui.logging.getLogger'):
                    panel._apply(None)
                assert message.call_count == 1
                assert str(failure) in ' '.join(panel.feedback.GetLabel().split()), repr(panel.feedback.GetLabel())
                assert panel.feedback.GetForegroundColour().Get() == MISSING_COLOUR.Get()
                assert read_setup(root) == saved, 'A failed save must not change files'
                assert panel.apply_button.IsEnabled() and panel.dirty
            panel.load(read_setup(project), read_setup(project).metadata, editor_major=9)
            assert not panel.feedback.GetLabel(), 'Feedback from another project must be cleared'
        finally:
            panel.Destroy()

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

        def accept_review():
            dialogs = [w for w in wx.GetTopLevelWindows() if isinstance(w, TemplateReviewDialog) and w.IsModal()]
            assert len(dialogs) == 1
            dialog = dialogs[0]
            for category in ('documents', 'template'):
                dialog.show_category(category)
                for row in range(dialog.files.GetItemCount()):
                    dialog.files.Select(row)
                dialog._refresh_selection()
                choose_action(dialog, 'use')
            document = 'kibot_resources/templates/assembly_notes.txt'
            dialog.select_path(document)
            dialog.editor.SetValue('Project-specific assembly notes\n')
            assert dialog.choices[document] == 'edit'
            capture(dialog, args.output.with_name('template-review.png'))
            with patch('project_manager.template_ui.PreviewDialog', side_effect=AssertionError('Unexpected preview')):
                event = wx.CommandEvent(wx.EVT_BUTTON.typeId, dialog.apply_button.GetId())
                dialog.apply_button.GetEventHandler().ProcessEvent(event)
            assert dialog.IsModal()
            assert dialog.applied
            # Closing after a direct save must refresh the parent without
            # applying the old plan again or treating Close as an undo.
            dialog.EndModal(wx.ID_CANCEL)

        wx.CallLater(150, accept_review)
        print('GUI: applying template review directly, then closing it', flush=True)
        frame._review_template(None)

    def check_review_selection():
        print('GUI: checking filtered range selection, partial apply and deferred drafts', flush=True)
        source = template_folder(base / 'selection-template')
        project = base / 'selection-project'
        shutil.copytree(source.root, project)
        theme = 'kibot_resources/colors/theme.json'
        script = 'kibot_resources/scripts/helper.py'
        document = 'kibot_resources/templates/assembly_notes.txt'
        missing = 'Logos/white.png'
        (project / theme).write_text('{"theme": "project"}\n')
        (project / script).write_text('print("project override")\n')
        (project / document).write_text('Project notes\n')
        (project / missing).unlink()
        comparison = compare(project, source)
        applied_plans = []
        def apply_preview(plan):
            applied_plans.append(plan)
            return apply_upgrade(plan, base / 'selection-backups')
        with TemplateReviewDialog(frame, comparison, apply_plan=apply_preview) as dialog:
            def exercise():
                try:
                    assert not dialog.files.HasFlag(wx.LC_SINGLE_SEL)
                    select_files(dialog, document)
                    dialog.editor.SetValue('Edited project notes\n')
                    # A stale single-file edit event must not spread a draft
                    # over another selected document before deferred refresh.
                    other = README_SOURCE
                    other_index = next(i for i, item in enumerate(comparison.items) if item.path == other)
                    dialog.files.Select(dialog.visible_indices.index(other_index))
                    choose_action(dialog, 'edit')
                    assert dialog.edits[document] == b'Edited project notes\n'
                    assert dialog.choices[other] == 'keep' and other not in dialog.edits
                    assert not dialog.editor.IsEnabled() and 'edit' not in dialog.actions
                    select_files(dialog, theme, script)
                    assert dialog.selection_label.GetLabel() == '2 selected files'
                    assert 'edit' not in dialog.actions
                    choose_action(dialog, 'use')
                    assert dialog.choices[theme] == dialog.choices[script] == 'use'
                    # Include a missing file: Keep must disappear for the batch.
                    index = next(i for i, item in enumerate(comparison.items) if item.path == missing)
                    dialog.files.Select(dialog.visible_indices.index(index))
                    dialog._refresh_selection()
                    assert 'keep' not in dialog.actions
                    choose_action(dialog, 'review')
                    assert all(dialog.choices[path] == 'review' for path in (theme, script, missing))
                    select_files(dialog, theme)
                    choose_action(dialog, 'use')
                    select_files(dialog)
                    assert not dialog.action.IsEnabled()
                    select_files(dialog, theme, script)
                    wx.CallLater(150, preview)
                except Exception:
                    traceback.print_exc()
                    failures.append('Filtered selection failed')
                    dialog.EndModal(wx.ID_CANCEL)

            def preview():
                try:
                    capture(dialog, args.output.with_name('template-bulk-selection.png'))
                    def accept_preview():
                        previews = [w for w in wx.GetTopLevelWindows() if isinstance(w, PreviewDialog) and w.IsModal()]
                        assert len(previews) == 1
                        capture(previews[0], args.output.with_name('template-partial-preview.png'))
                        previews[0].EndModal(wx.ID_OK)
                    wx.CallLater(150, accept_preview)
                    with patch('project_manager.template_ui.wx.MessageBox', side_effect=AssertionError('Unexpected error')):
                        dialog._preview(None)
                    assert dialog.IsModal(), 'Preview Apply closed template review'
                    assert dialog.applied and len(applied_plans) == 1
                    assert 'Changes applied' in dialog.feedback.GetLabel()
                    assert (project / document).read_text() == 'Edited project notes\n'
                    dialog.EndModal(wx.ID_CANCEL)
                except Exception:
                    traceback.print_exc()
                    failures.append('Partial preview failed')
                    dialog.EndModal(wx.ID_CANCEL)

            wx.CallLater(150, exercise)
            assert dialog.ShowModal() == wx.ID_CANCEL
            plan = applied_plans[0]
            assert {change.relative for change in plan.changes} == {theme, document, STATE}
        assert (project / theme).read_bytes() == source.files[theme]
        assert (project / document).read_text() == 'Edited project notes\n'
        assert (project / script).read_text() == 'print("project override")\n'
        assert not (project / missing).exists()
        assert {item.path for item in compare(project, source).problems} == {script, missing}
        storage = base / 'direct-template-backups'
        verified = []
        with TemplateReviewDialog(frame, compare(project, source), apply_plan=lambda plan:
                                  apply_upgrade(plan, storage, verify=lambda: verified.append(True))) as dialog:
            def exercise_apply():
                try:
                    assert dialog.apply_button.GetScreenRect().x > dialog.preview_button.GetScreenRect().right
                    def choose(path, action):
                        dialog.select_path(path)
                        choose_action(dialog, action)
                    def click_apply():
                        with patch('project_manager.template_ui.PreviewDialog', side_effect=AssertionError('Unexpected preview')), \
                             patch('project_manager.template_ui.wx.MessageBox', side_effect=AssertionError('Unexpected warning')):
                            event = wx.CommandEvent(wx.EVT_BUTTON.typeId, dialog.apply_button.GetId())
                            dialog.apply_button.GetEventHandler().ProcessEvent(event)
                        assert dialog.IsModal()
                    choose(document, 'edit')
                    dialog.editor.SetValue('Deferred document edit\n')
                    choose(document, 'review')
                    choose(script, 'use')
                    click_apply()
                    assert dialog.applied and verified
                    assert (project / script).read_bytes() == source.files[script]
                    assert dialog.choices[missing] == dialog.choices[document] == 'review'
                    assert dialog.edits[document] == b'Deferred document edit\n'
                    assert (project / document).read_text() == 'Edited project notes\n'
                    backups = list(storage.rglob('manifest.json'))
                    click_apply()
                    assert list(storage.rglob('manifest.json')) == backups
                    choose(missing, 'use')
                    click_apply()
                    assert (project / missing).read_bytes() == source.files[missing]
                    assert not dialog.comparison.problems
                    wx.CallLater(150, finish)
                except Exception:
                    traceback.print_exc()
                    failures.append('Direct template Apply failed')
                    dialog.EndModal(wx.ID_CANCEL)
            def finish():
                capture(dialog, args.output.with_name('template-direct-apply.png'))
                dialog.EndModal(wx.ID_CANCEL)
            wx.CallLater(150, exercise_apply)
            assert dialog.ShowModal() == wx.ID_CANCEL and dialog.applied

    def assert_template_layout():
        label = frame.template_label.GetScreenRect()
        path = frame.template_path.GetScreenRect()
        assert abs((label.y + label.height / 2) - (path.y + path.height / 2)) <= 2
        status = frame.template_status.GetScreenRect()
        buttons = frame.template_details_button.GetScreenRect()
        assert status.y + status.height <= buttons.y, 'Template status overlaps buttons'
        for line in frame.template_status.GetLabel().splitlines():
            width = frame.template_status.GetTextExtent(line).width
            assert width <= status.width, (
                f'Template status clips horizontally: {width} > {status.width}; '
                f'client={frame.template_status.GetClientSize()}, '
                f'wrapped_at={wrap_widths.get(frame.template_status.GetId())}: {line!r}')

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
                assert 'board_file' not in frame.fields
                assert frame.fields["project_file"].GetValue() == "—"
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
                check_ci_version_migration()
                check_document_workspace()
                check_review_refresh()
                check_impedance_editor()
                check_accepted_license()
                check_review_selection()
                review_template()
                phase = 31
            elif phase == 31:
                saved = read_setup(project)
                assert b'kibot_variant: DRAFT  # existing comment' in saved.workflow
                assert (project / STATE).is_file()
                assert frame.template_comparison.colour == 'green'
                assert 'Up to date' in frame.template_status.GetLabel()
                assert '1 accepted customization' in frame.template_status.GetLabel()
                assert frame.template_status.GetForegroundColour().Get() == FOUND_COLOUR.Get()
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
                panel = frame.setup_panel
                assert panel.feedback.GetLabel() == 'Changes saved successfully.', 'Save result lost during refresh'
                assert panel.feedback.GetForegroundColour().Get() == MATCH_COLOUR.Get()
                frame.notebook.SetSelection(1)
                frame.Layout()
                wx.YieldIfNeeded()
                assert panel.feedback.GetScreenRect().bottom <= panel.GetScreenRect().bottom
                capture(frame, args.output.with_name('setup-saved.png'))
                panel.fields['PROJECT_NAME'].SetValue('')
                assert not panel.feedback.GetLabel()
                with patch('project_manager.setup_ui.wx.MessageBox') as message:
                    panel._apply(None)
                assert message.call_count == 1
                assert 'Could not save changes' in panel.feedback.GetLabel()
                assert panel.feedback.GetForegroundColour().Get() == MISSING_COLOUR.Get()
                assert read_setup(project) == saved
                capture(frame, args.output.with_name('setup-error.png'))
                panel.fields['PROJECT_NAME'].SetValue(saved.metadata['PROJECT_NAME'])
                with patch('project_manager.setup_ui.wx.MessageBox', side_effect=AssertionError('Unexpected no-op popup')):
                    panel._apply(None)
                assert panel.feedback.GetLabel() == 'No changes to save.'
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
                index = next(i for i, preset in enumerate(panel.catalog.stackups, 1) if preset.id == 'example_six_layer')
                panel.choices['stackups'].SetSelection(index)
                panel._selection_changed()
                assert len(panel.current_stackup.stack.copper) == 2
                assert len(panel.selected_stackup.stack.copper) == 6
                assert not panel.capture_button.IsShown(), 'The saved board matches the catalog regardless of the preview selection'
                assert any(p.label == 'D3.2' for p in panel.selected_stackup.stack.layers)
                assert 'Example Fab' in panel.selected('stackups').name
                assert str(standards) in frame.standards_path.GetValue()
                for removed in ('compare_button', 'preview_button', 'comparison_pane', 'summary', 'diff'):
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
                assert panel.feedback.GetForegroundColour().Get() == MODIFIED_COLOUR.Get()
                assert std_board.read_bytes() == before[std_board]
                frame.SetSize((860, 640))
                phase = 806
            elif phase == 806:
                panel = frame.stackup_panel
                for line in panel.feedback.GetLabel().splitlines():
                    width = panel.feedback.GetTextExtent(line).width
                    assert width <= panel.feedback.GetSize().width, f'Feedback text clips: {width} > {panel.feedback.GetSize().width}: {line}'
                assert panel.feedback.GetScreenRect().bottom <= panel.GetScreenRect().bottom, (
                    f'Feedback below viewport: {panel.feedback.GetScreenRect()}, panel {panel.GetScreenRect()}')
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
                assert panel.feedback.GetForegroundColour().Get() == MATCH_COLOUR.Get()
                assert 'Example Fab · EX0616' in panel.current_stackup.title.GetLabel()
                assert 'settings differ' not in panel.current_stackup.title.GetLabel()
                assert not panel.capture_button.IsShown(), 'Applying a catalog stackup must hide Save as preset'
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
                assert panel.feedback.GetForegroundColour().Get() == MATCH_COLOUR.Get()
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
                assert frame.template_status.GetForegroundColour().Get() == MISSING_COLOUR.Get()
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
            traceback.print_exc()
            failures.append(str(exc) or type(exc).__name__)
            try:
                capture(frame, args.output.with_name('gui-failure.png'))
            except Exception:
                traceback.print_exc()
            frame.Destroy()

    wx.CallLater(250, check)
    # The phase timeout cannot fire while a failed callback leaves a modal dialog
    # open. Bound the entire event loop and print thread stacks on a stuck test.
    faulthandler.dump_traceback_later(45, exit=True)
    with patch('project_manager.ui._wrap_text', side_effect=measured_wrap):
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
