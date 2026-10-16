from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from project_manager.context import ContextError, context_from_document, detect_context


def document(directory="", name="", board=""):
    return NS(project=NS(path=directory, name=name), board_filename=board)


class ContextTests(unittest.TestCase):
    def test_project_identity_and_optional_files(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sample.v1.kicad_pro").write_text("{}")
            (root / "sample.v1.kicad_pcb").write_text("(kicad_pcb)")
            (root / "kibot_yaml").mkdir()
            (root / "kibot_yaml/kibot_main.yaml").write_text("kibot: {version: 1}")
            context = context_from_document(document(folder, "sample.v1", "sample.v1.kicad_pcb"), "9.0.9")
            self.assertEqual(context.project_name, "sample.v1")
            self.assertEqual(context.board_file, str(root / "sample.v1.kicad_pcb"))
            self.assertTrue(context.project_saved and context.board_saved)
            self.assertTrue(context.kibot_config_present)
            self.assertFalse(context.workflow_present)

    def test_unsaved_board_never_uses_current_directory(self):
        with self.assertRaises(ContextError):
            context_from_document(document(board="untitled.kicad_pcb"), "9.0.9")

    def test_setup_paths_are_relative_to_project_when_board_is_in_subdirectory(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'kibot_yaml').mkdir()
            (root / 'kibot_yaml/kibot_main.yaml').write_text('kibot: {version: 1}')
            context = context_from_document(document(folder, 'sample', 'boards/sample.kicad_pcb'), '9.0.9')
            self.assertTrue(context.kibot_config_present)

    def test_standalone_board_does_not_invent_project(self):
        with TemporaryDirectory() as folder:
            context = context_from_document(document(board=str(Path(folder) / "standalone.kicad_pcb")), "9.0.9")
            self.assertEqual(context.project_file, "")
            self.assertFalse(context.project_saved)

    def test_launch_requires_explicit_endpoint(self):
        with self.assertRaisesRegex(ContextError, "External Plugins"):
            detect_context(environ={})

    def test_uses_launching_socket_and_token(self):
        with TemporaryDirectory() as folder:
            observed = {}

            def factory(**kwargs):
                observed.update(kwargs)
                return NS(
                    get_version=lambda: NS(major=9, full_version="9.0.9"),
                    get_open_documents=lambda kind: [document(folder, "sample", "sample.kicad_pcb")],
                )

            result = detect_context(environ={"KICAD_API_SOCKET": "/tmp/editor-123.sock", "KICAD_API_TOKEN": "test-token"}, client_factory=factory)
            self.assertEqual(observed["socket_path"], "ipc:///tmp/editor-123.sock")
            self.assertEqual(observed["kicad_token"], "test-token")
            self.assertNotIn("test-token", str(result.to_dict()))

    def test_no_board_and_multiple_boards_are_not_guessed(self):
        for documents in ([], [document(), document()]):
            with self.subTest(count=len(documents)):
                factory = lambda **kw: NS(get_version=lambda: NS(major=9, full_version="9.0.9"), get_open_documents=lambda kind: documents)
                with self.assertRaises(ContextError):
                    detect_context("ipc:///test", client_factory=factory, environ={})

    def test_other_kicad_major_is_rejected(self):
        factory = lambda **kw: NS(get_version=lambda: NS(major=10, full_version="10.0.1"))
        with self.assertRaisesRegex(ContextError, "KiCad 9 only"):
            detect_context("ipc:///test", client_factory=factory, environ={})

    def test_transport_failure_has_no_secrets(self):
        def factory(**kw):
            raise RuntimeError("secret-token in transport error")
        with self.assertRaises(ContextError) as result:
            detect_context("ipc:///test", client_factory=factory, environ={})
        self.assertNotIn("secret-token", str(result.exception))


if __name__ == "__main__":
    unittest.main()
