"""CSV editing/imports preserve project rows and use the saved construction."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from project_manager.impedance_table import HEADERS, ImpedanceTable, add_missing_guides, matching_guides
from project_manager.standards import Preset
from project_manager.yaml_fields import SetupError
from tests.standards_fixtures import STACKUP, catalog_folder, saved_board, write_stackup


def profile(**changes):
    return {'mode': 'differential', 'impedance_ohms': 100, 'geometry': 'non-coplanar microstrip',
            'track_width_mm': 0.2146, 'pair_gap_mm': 0.2032, 'layer': 'F.Cu',
            'reference_layer': 'B.Cu', **changes}


def preset(*targets):
    return Preset('test', 'Test', 'stackups', '', {'copper_layer_count': 2,
                  'impedance_targets': list(targets)}, {})


class ImpedanceTableTests(unittest.TestCase):
    def test_legacy_tabs_and_csv_quotes_round_trip_without_manual_alignment(self):
        value = ',\t\t'.join(HEADERS) + '\n"Coated, coupled microstrip",100,±10 %,L1,0.2146,0.2032,L2\n'
        table = ImpedanceTable.parse(value)
        self.assertEqual(table.rows[0][0], 'Coated, coupled microstrip')
        self.assertEqual(table.rows[0][2], '±10 %')
        self.assertNotIn('\t', table.render())
        self.assertEqual(ImpedanceTable.parse(table.render()), table)
        self.assertEqual(ImpedanceTable.parse(','.join(HEADERS)+'\n').rows, ())

    def test_unknown_or_malformed_formats_are_left_to_the_text_editor(self):
        for value in ('README,notes\n', ','.join(HEADERS)+'\nonly,two\n',
                      ','.join(HEADERS)+'\n"unclosed quote\n'):
            with self.assertRaises(SetupError):
                ImpedanceTable.parse(value)

    def test_import_appends_missing_profile_without_overwriting_manual_50_ohms(self):
        manual = ('Custom single-ended', '50', '±5 %', 'L1', '0.32', '', 'L2')
        table = ImpedanceTable(HEADERS, (manual,))
        updated, added, conflicts = add_missing_guides(table, preset(profile()))
        self.assertEqual(added, 1)
        self.assertFalse(conflicts)
        self.assertEqual(updated.rows[0], manual)
        self.assertEqual(updated.rows[1][2], '')  # No invented impedance tolerance.
        self.assertEqual(add_missing_guides(updated, preset(profile())), (updated, 0, ()))

    def test_equivalent_layer_and_numeric_spellings_do_not_duplicate_existing_rows(self):
        row = ('Edge-Coupled Coated Microstrip', '100.0', '±10 %', 'F.Cu', '0.214600', '0.203200', 'B.Cu')
        table = ImpedanceTable(HEADERS, (row,))
        self.assertEqual(add_missing_guides(table, preset(profile())), (table, 0, ()))

    def test_import_does_not_round_catalog_geometry(self):
        table, _, _ = add_missing_guides(ImpedanceTable(HEADERS, ()), preset(profile(
            track_width_mm=0.123456789, pair_gap_mm=0.0987654321)))
        self.assertEqual(table.rows[0][4:6], ('0.123456789', '0.0987654321'))

    def test_geometry_conflict_is_reported_and_existing_data_is_preserved(self):
        row = ('Manual differential', '100', '±10 %', 'L1', '0.3', '0.2', 'L2')
        table = ImpedanceTable(HEADERS, (row,))
        updated, added, conflicts = add_missing_guides(table, preset(profile(), profile(
            mode='single_ended', impedance_ohms=50, track_width_mm=0.35, pair_gap_mm=None)))
        self.assertEqual(updated.rows[0], row)
        self.assertEqual(added, 1)
        self.assertEqual(len(conflicts), 1)
        self.assertIn('existing width/gap differs', conflicts[0])

    def test_unspecified_signal_or_reference_layers_are_not_invented(self):
        table = ImpedanceTable(HEADERS, ())
        updated, added, conflicts = add_missing_guides(table, preset(profile(layer='')))
        self.assertEqual(updated, table)
        self.assertEqual(added, 0)
        self.assertIn('standards profile first', conflicts[0])

    def test_import_candidates_only_match_the_saved_board_construction(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = catalog_folder(base / 'standards')
            _, board = saved_board(base / 'board')
            write_stackup(root / 'stackups/two_layer.json', 'two_layer', 'Matching', STACKUP,
                          impedance_targets=[profile()])
            write_stackup(root / 'stackups/other.json', 'different', 'Different',
                          STACKUP.replace('0.035', '0.07'), impedance_targets=[profile()])
            self.assertEqual([p.id for p in matching_guides(root, board)], ['two_layer'])
            board.write_text(board.read_text().replace('0.035', '0.05'))
            self.assertFalse(matching_guides(root, board))


if __name__ == '__main__':
    unittest.main()
