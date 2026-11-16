"""Verify Excel tables using a case with all three RRH categories."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import select_bbu_pools as planner


class PoolWorkbookTests(unittest.TestCase):
    def test_pool_lists_summary_and_alternatives_without_charts(self):
        rrhs = [planner.RRH("only_left", (0, 0), False),
                planner.RRH("=shared", (.01, 0), False, "office"),
                planner.RRH("only_right", (.1, 0), True)]
        with tempfile.TemporaryDirectory() as temporary, patch.object(planner, "MAX_LINK_DISTANCE_KM", 10.5):
            directory = Path(temporary)
            links = planner.build_links(rrhs, (0, 2))
            history = [planner.search_record("initial", {1, 2}, [0, planner.distance_km((0, 0), (.01, 0)), 0])]
            planner.write_min_delay_results(directory, rrhs, 2, (0, 2), links, {1, 2}, history)
            self.assertFalse((directory / "pool_categories.csv").exists())
            book = load_workbook(directory / planner.POOL_WORKBOOK_FILE)
            self.addCleanup(book.close)
            self.assertEqual(set(book.sheetnames), {"Read me", "Summary", "BBU_01", "BBU_02"})
            self.assertEqual(list(book["Summary"].values)[1:], [
                ("initial", "BBU_01", 1, 1, 0, 2),
                ("initial", "BBU_02", 1, 0, 1, 2),
                ("final", "BBU_01", 1, 1, 0, 2),
                ("final", "BBU_02", 1, 0, 1, 2),
            ])
            self.assertTrue(all(not sheet._charts for sheet in book.worksheets))
            for pool_id, expected_category, alternative in (
                ("BBU_01", "2. Optimal pool with alternatives", "BBU_02"),
                ("BBU_02", "3. Feasible but not optimal", "BBU_01"),
            ):
                sheet = book[pool_id]
                shared = [row for row in sheet.iter_rows(min_row=2) if row[2].value == "=shared"]
                self.assertEqual(len(shared), 2)
                for row in shared:
                    self.assertEqual(row[1].value, expected_category)
                    self.assertEqual(row[2].data_type, "s")
                    self.assertEqual(row[8].value, "BBU_01")
                    self.assertEqual(row[9].value, 2)
                    self.assertEqual(row[10].value, alternative)
                    self.assertEqual(row[11].value, "BBU_01;BBU_02")
                self.assertEqual(sheet.freeze_panes, "C2")
                self.assertEqual(len(sheet.tables), 1)


if __name__ == "__main__":
    unittest.main()
