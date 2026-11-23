"""Pure-logic checks for print_document and nearest_spot_colors (no CorelDRAW needed)."""

import tempfile
import unittest
from pathlib import Path

from tools import printing, spots


class CieDe2000Tests(unittest.TestCase):
    def test_sharma_reference_pairs(self):
        # Pairs from Sharma, Wu and Dalal (2005), the CIEDE2000 test data.
        self.assertAlmostEqual(spots.ciede2000((50, 2.6772, -79.7751), (50, 0, -82.7485)), 2.0425, places=3)
        self.assertAlmostEqual(spots.ciede2000((50, 2.5, 0), (50, 0, -2.5)), 4.3065, places=3)
        self.assertAlmostEqual(spots.ciede2000((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387)),
                               1.2644, places=3)

    def test_identical_is_zero(self):
        self.assertEqual(spots.ciede2000((40, 10, 10), (40, 10, 10)), 0)

    def test_rank_is_sorted_and_deterministic(self):
        colors = (("B", (50, 0, 0), "#777777"), ("A", (50, 0, 0), "#777777"), ("far", (90, 0, 0), "#EEEEEE"))
        found = spots.rank((52, 0, 0), colors, 2)
        self.assertEqual([row["name"] for row in found], ["A", "B"])

    def test_palette_key_keeps_plus(self):
        index = {"palettes": [("PANTONE® solid coated", "g1", "old"), ("PANTONE+ Solid Coated-V5", "g2", "new")]}
        self.assertEqual(spots.find_palette(index, None)[1], "g2")
        self.assertEqual(spots.find_palette(index, "pantone solid coated")[1], "g1")
        with self.assertRaises(ValueError):
            spots.find_palette(index, "nope")

    def test_palette_xml_lab(self):
        xml = ('<palette name="t" guid="g"><colorspaces><cs name="X"><color cs="LAB" tints="0.5,0.5,0.5"/>'
               '<color cs="RGB" tints="1,0,0"/></cs></colorspaces></palette>')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "p.xml")
            path.write_text(xml)
            (name, lab, hex_value), = spots._read_palette(str(path), 0.0)
        self.assertEqual((name, hex_value), ("X", "#FF0000"))
        self.assertAlmostEqual(lab[0], 50)
        self.assertAlmostEqual(lab[1], -0.5)


class PrintPlanTests(unittest.TestCase):
    def inventory(self, print_plates, registration_only=(), blank=()):
        return {"page_index": 1, "plates": {"print": print_plates, "registration_only": list(registration_only),
                                            "blank_spot_plates": list(blank)}}

    def test_page_range_text(self):
        self.assertEqual(printing.page_range_text([1, 2, 3, 5]), "1-3,5")
        self.assertEqual(printing.page_range_text([4]), "4")

    def test_check_pages(self):
        self.assertEqual(printing.check_pages([3, 1], 3), [1, 3])
        for bad in ([], [0], [4], [1, 1], ["1"], [True]):
            with self.assertRaises(ValueError):
                printing.check_pages(bad, 3)

    def test_registration_only_process_plates_are_not_output(self):
        inv = self.inventory(["A", "Cyan", "Magenta"], registration_only=["Cyan", "Magenta"])
        plan = printing.plan_page(inv, {"A": True, "Cyan": True, "Magenta": True}, True, False)
        self.assertEqual(plan["plates"], ["A"])
        self.assertEqual(plan["warnings"][0]["code"], "process_plates_registration_only")

    def test_process_art_warns(self):
        plan = printing.plan_page(self.inventory(["A", "Cyan"]), {"A": True, "Cyan": True}, True, False)
        self.assertEqual(plan["plates"], ["A", "Cyan"])
        self.assertIn("process_plates_present", [w["code"] for w in plan["warnings"]])

    def test_disabled_and_empty_plates(self):
        plan = printing.plan_page(self.inventory(["A", "B"], blank=["E"]), {"A": True, "B": False, "E": True},
                                  True, True)
        self.assertEqual(plan["plates"], ["A", "E"])
        codes = [w["code"] for w in plan["warnings"]]
        self.assertIn("used_plate_disabled", codes)
        self.assertIn("empty_plates_print", codes)

    def test_composite(self):
        plan = printing.plan_page(self.inventory(["A"]), {}, False, False)
        self.assertEqual(len(plan["plates"]), 1)
        self.assertIn("Composite", plan["plates"][0])


class LedgerTests(unittest.TestCase):
    def test_duplicate_key_is_refused_and_release_allows_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "nested", "ledger.json")
            self.assertIsNone(printing.reserve_submission("k", {"n": 1}, path))
            self.assertEqual(printing.reserve_submission("k", {"n": 2}, path), {"n": 1})
            printing.update_submission("k", {"status": "submitted"}, path)
            self.assertEqual(printing.reserve_submission("k", {"n": 3}, path)["status"], "submitted")
            printing.release_submission("k", path)
            self.assertIsNone(printing.reserve_submission("k", {"n": 4}, path))


class ArgumentTests(unittest.TestCase):
    def test_live_runs_need_a_key(self):
        result = printing.print_document("x.cdr", "P", [1], True, dry_run=False)
        self.assertFalse(result.success)
        self.assertIn("submission_key", result.error)

    def test_separations_is_required_bool(self):
        self.assertFalse(printing.print_document("x.cdr", "P", [1], "yes").success)


if __name__ == "__main__":
    unittest.main()
