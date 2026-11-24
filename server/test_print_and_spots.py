"""Pure-logic checks for print_document and nearest_spot_colors (no CorelDRAW needed)."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


class PlateChoiceTests(unittest.TestCase):
    PLAN = [{"page_index": 1, "plates": ["White", "PANTONE 186 C", "Black"], "warnings": []}]
    STATE = {"White": True, "PANTONE 186 C": True, "Black": True, "Cyan": False}

    def test_keeps_the_manifest_order(self):
        self.assertEqual(printing.check_plates(["Black", "White"], self.PLAN, self.STATE), ["White", "Black"])
        only = printing.only_plates(self.PLAN, ["PANTONE 186 C"])
        self.assertEqual(only[0]["plates"], ["PANTONE 186 C"])

    def test_refuses_names_the_pages_do_not_output(self):
        for bad in ([], ["Cyan"], ["White", "White"], [""], "White", [1]):
            with self.assertRaises(ValueError):
                printing.check_plates(bad, self.PLAN, self.STATE)

    def test_refuses_a_subset_when_a_plate_cannot_be_switched(self):
        with self.assertRaisesRegex(ValueError, "not in the print settings"):
            printing.check_plates(["White"], self.PLAN, {"White": True, "PANTONE 186 C": True})


class Marks:
    """CorelDRAW marks a document changed when its print settings or plate switches are set, even back again."""

    owner = None

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if Marks.owner is not None and name[:1].isupper():
            Marks.owner.Dirty = True


class FakePlate(Marks):
    def __init__(self, color, enabled):
        self.Color, self.Enabled = color, enabled


class FakePlates:
    def __init__(self, plates):
        self.plates = plates
        self.Count = len(plates)

    def Item(self, index):
        return self.plates[index - 1]


class FakeSettings(Marks):
    """CorelDRAW's PrintSettings, enough for print_document."""

    def __init__(self, plates):
        self.Printer = SimpleNamespace(Name="Office")
        self.PrintRange, self.PageRange, self.Copies, self.PrintToFile, self.FileName = 0, "", 1, False, ""
        self.Separations = SimpleNamespace(Enabled=False, EmptyPlates=False, Plates=FakePlates(plates))
        self.printed = []

    def SelectPrinter(self, name):
        self.Printer.Name = name
        self.Copies = self.Copies  # marks the document changed, as CorelDRAW does

    def PrintOut(self):
        self.printed.append({str(p.Color): p.Enabled for p in self.Separations.Plates.plates})


class PlateSubmissionTests(unittest.TestCase):
    """print_document with plates against a fake COM document (no CorelDRAW needed)."""

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.settings = FakeSettings([FakePlate("White", True), FakePlate("PANTONE 186 C", True),
                                      FakePlate("Black", True), FakePlate("Cyan", False)])
        doc = SimpleNamespace(Pages=SimpleNamespace(Count=2), PrintSettings=self.settings, Dirty=False, Unit=0)
        self.doc = doc
        Marks.owner = doc
        self.addCleanup(setattr, Marks, "owner", None)

        def safe_call(fn):  # as the bridge does: an exception becomes a failed call
            try:
                return {"success": True, "result": fn()}
            except Exception as exc:
                return {"success": False, "error": str(exc)}

        conn = SimpleNamespace(app=None, safe_call=safe_call)
        inventory = {"page_index": 1, "plates": {"print": ["White", "PANTONE 186 C", "Black"],
                                                 "registration_only": [], "blank_spot_plates": []}}
        for target, value in ((printing, ("get_connection", lambda: conn)),
                              (printing, ("_installed_printers", lambda: ["RIP"])),
                              (printing, ("resolve_document", lambda app, path, activate: (doc, "C:/x.cdr")))):
            patcher = patch.object(target, value[0], value[1])
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("tools.inks._page_inventory", lambda *args: inventory),
                            ("core.targets.resolve_page", lambda doc, page: page),
                            ("core.units.unit_name", lambda code: "mm")):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"CORELDRAW_MCP_STATE_DIR": folder})
        env.start()
        self.addCleanup(env.stop)

    def state(self):
        return {str(p.Color): p.Enabled for p in self.settings.Separations.Plates.plates}

    def test_dry_run_plans_only_the_named_plates(self):
        result = printing.print_document("x.cdr", "RIP", [1], True, plates=["PANTONE 186 C"])
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.data["plates"], ["PANTONE 186 C"])
        self.assertEqual(result.data["plan"][0]["plates"], ["PANTONE 186 C"])
        self.assertEqual(self.settings.printed, [])

    def test_prints_one_plate_and_restores_every_plate(self):
        before = self.state()
        result = printing.print_document("x.cdr", "RIP", [1], True, dry_run=False, submission_key="t-e-p1-186",
                                         plates=["PANTONE 186 C"])
        self.assertTrue(result.success, result.error)
        self.assertEqual(self.settings.printed, [{"White": False, "PANTONE 186 C": True, "Black": False,
                                                   "Cyan": False}])
        self.assertEqual(self.state(), before)
        self.assertNotIn("warning", result.data)
        self.assertEqual(result.data["ledger_record"]["plates"], ["PANTONE 186 C"])
        again = printing.print_document("x.cdr", "RIP", [1], True, dry_run=False, submission_key="t-e-p1-186",
                                        plates=["PANTONE 186 C"])
        self.assertFalse(again.success)
        self.assertEqual(len(self.settings.printed), 1)

    def test_a_print_leaves_the_document_as_saved_so_the_next_plate_prints(self):
        first = printing.print_document("x.cdr", "RIP", [1], True, dry_run=False, submission_key="t-e-p1-f1-white",
                                        plates=["White"])
        self.assertTrue(first.success, first.error)
        self.assertFalse(self.doc.Dirty)
        self.assertNotIn("warning", first.data)
        second = printing.print_document("x.cdr", "RIP", [1], True, dry_run=False, submission_key="t-e-p1-f2-186",
                                         plates=["PANTONE 186 C"])
        self.assertTrue(second.success, second.error)
        self.assertEqual(len(self.settings.printed), 2)
        self.assertFalse(self.doc.Dirty)

    def test_a_document_whose_settings_were_not_restored_stays_marked_changed(self):
        def fail_to_restore(name):
            if name != "RIP":
                raise RuntimeError("printer not available")
            self.settings.Printer.Name = name

        object.__setattr__(self.settings, "SelectPrinter", fail_to_restore)  # not a print setting
        result = printing.print_document("x.cdr", "RIP", [1], True, dry_run=False, submission_key="t-e-p1-f1-white",
                                         plates=["White"])
        self.assertTrue(result.success, result.error)
        self.assertIn("not restored", result.data["warning"])
        self.assertTrue(self.doc.Dirty)
        refused = printing.print_document("x.cdr", "RIP", [1], True, dry_run=False, submission_key="t-e-p1-f2-186",
                                          plates=["PANTONE 186 C"])
        self.assertFalse(refused.success)
        self.assertIn("unsaved changes", refused.error)

    def test_refuses_an_unknown_plate_without_printing_or_reserving(self):
        result = printing.print_document("x.cdr", "RIP", [1], True, dry_run=False, submission_key="k",
                                         plates=["Cyan"])
        self.assertFalse(result.success)
        self.assertEqual(self.settings.printed, [])
        retry = printing.print_document("x.cdr", "RIP", [1], True, dry_run=False, submission_key="k",
                                        plates=["White"])
        self.assertTrue(retry.success, retry.error)

    def test_plates_need_separations(self):
        self.assertFalse(printing.print_document("x.cdr", "RIP", [1], False, plates=["White"]).success)


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
