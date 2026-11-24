"""publish_proof (export_page_proof_pdf, render_plates) leaves the document as it found it (no CorelDRAW needed)."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from tools import plates, printing, proof


class Marked:
    """A COM object whose property writes mark its document changed, as CorelDRAW does (even writing a value back)."""

    def __init__(self, doc, **values):
        object.__setattr__(self, "doc", doc)
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        self.doc.Dirty = True


class FakePages:
    def __init__(self, pages):
        self.pages = pages
        self.Count = len(pages)

    def __call__(self, index):
        return self.pages[index - 1]


class FakeDocument:
    def __init__(self):
        self.Dirty = False
        self.Unit = 3
        self.ActivePage = SimpleNamespace(Index=1)
        shapes = SimpleNamespace(Count=4)
        self.layers = [
            Marked(self, Name="Art", Master=False, IsSpecialLayer=False, Printable=True, Visible=True, Shapes=shapes),
            Marked(self, Name="Underbase", Master=False, IsSpecialLayer=False, Printable=True, Visible=False,
                   Shapes=shapes),
            Marked(self, Name="Notes", Master=False, IsSpecialLayer=False, Printable=False, Visible=False,
                   Shapes=shapes),
        ]
        self.Pages = FakePages([SimpleNamespace(AllLayers=self.layers)])
        self.PDFSettings = Marked(self, PublishRange=0, PageRange="", ColorMode=1, OutputSpotColorsAs=1,
                                  Overprints=False, CropMarks=True, RegistrationMarks=True)
        self.published = []

    def PublishToPDF(self, path):  # noqa: N802 (the COM method)
        self.published.append({layer.Name: layer.Visible for layer in self.layers})
        Path(path).write_bytes(b"%PDF-1.7\n")


class PublishProofTests(unittest.TestCase):
    def setUp(self):
        self.doc = FakeDocument()
        self.other = SimpleNamespace(name="another open document")
        self.app = SimpleNamespace(ActiveDocument=self.other)

        def resolve(app, path, activate=False):
            if activate:
                app.ActiveDocument = self.doc
            return self.doc, "C:/jobs/x.cdr"

        def activate(app, doc):
            app.ActiveDocument = doc

        for name, value in (("resolve_document", resolve), ("activate_document", activate),
                            ("is_active", lambda app, doc: app.ActiveDocument is doc)):
            patcher = patch.object(proof, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_hidden_printable_layers_print_and_the_document_is_left_as_it_was(self):
        pdf_settings = dict(vars(self.doc.PDFSettings))
        result = proof.publish_proof(self.app, "C:/jobs/x.cdr", 1, self.folder / "proof.pdf",
                                     include_hidden_printable=True)
        self.assertEqual(result["hidden_printable_layers_included"], ["Underbase (page)"])
        # The hidden printable layer was shown while publishing, and only then.
        self.assertEqual(self.doc.published, [{"Art": True, "Underbase": True, "Notes": False}])
        self.assertEqual([layer.Visible for layer in self.doc.layers], [True, False, False])
        self.assertEqual(vars(self.doc.PDFSettings), pdf_settings)
        # Not marked changed, so print_document still prints it; the active document is the one active before.
        self.assertFalse(self.doc.Dirty)
        self.assertIs(self.app.ActiveDocument, self.other)
        self.assertEqual(self.doc.ActivePage.Index, 1)

    def test_a_document_changed_before_stays_changed(self):
        self.doc.Dirty = True
        proof.publish_proof(self.app, "C:/jobs/x.cdr", 1, self.folder / "proof.pdf", include_hidden_printable=True)
        self.assertTrue(self.doc.Dirty)

    def test_a_plain_proof_does_not_activate_the_document(self):
        proof.publish_proof(self.app, "C:/jobs/x.cdr", 1, self.folder / "proof.pdf")
        self.assertIs(self.app.ActiveDocument, self.other)
        self.assertFalse(self.doc.Dirty)
        self.assertEqual(self.doc.published, [{"Art": True, "Underbase": False, "Notes": False}])


class PrintRenderPrintTests(unittest.TestCase):
    """The film printing task's order on one open document: a dry-run print, a real print of one plate, render_plates
    of the page, a real print of the next plate. None may leave the document marked changed (print_document refuses
    a changed document), so no step is refused and nothing needs reopening."""

    def setUp(self):
        from test_print_and_spots import FakePlate, FakeSettings, Marks

        self.doc = FakeDocument()
        self.settings = FakeSettings([FakePlate("White", True), FakePlate("PANTONE 186 C", True)])
        self.doc.PrintSettings = self.settings
        Marks.owner = self.doc  # print settings and plate switches mark it changed, as layers and PDF settings do
        self.addCleanup(setattr, Marks, "owner", None)
        self.app = SimpleNamespace(ActiveDocument=self.doc)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

        def call(fn):
            try:
                return {"success": True, "result": fn()}
            except Exception as exc:
                return {"success": False, "error": str(exc)}

        conn = SimpleNamespace(app=self.app, safe_call=call, read_call=call)
        page = self.doc.Pages(1)
        inventory = {"page_index": 1, "plates": {"print": ["White", "PANTONE 186 C"], "registration_only": [],
                                                 "blank_spot_plates": []}}
        page_data = {"page_bounds": {"left": 0.0, "bottom": 0.0, "right": 2.0, "top": 3.0}, "objects": [],
                     "layers": []}
        ink = np.zeros((108, 72), np.uint8)
        ink[10:40, 10:60] = 255
        rendered = (Image.new("RGB", (72, 108), "white"), [("White", ink), ("PANTONE 186 C", ink[::-1].copy())])
        for target, name, value in (
            (printing, "get_connection", lambda: conn),
            (printing, "_installed_printers", lambda: ["RIP"]),
            (printing, "resolve_document", lambda app, path, activate=False: (self.doc, "C:/jobs/x.cdr")),
            (plates, "get_connection", lambda: conn),
            (plates, "resolve_document", lambda app, path, activate=False: (self.doc, "C:/jobs/x.cdr")),
            (plates, "resolve_page", lambda doc, index: page),
            (plates, "unit_name", lambda code: "in"),
            (plates, "_page_inventory", lambda *args, **kwargs: page_data),
            (plates.seps, "find_ghostscript", lambda: Path("gs")),
            (plates.seps, "render_tiffsep", lambda pdf, work, ghostscript, dpi: rendered),
            (proof, "resolve_document", lambda app, path, activate=False: (self.doc, "C:/jobs/x.cdr")),
            (proof, "resolve_page", lambda doc, index: page),
            (proof, "is_active", lambda app, doc: app.ActiveDocument is doc),
            (proof, "activate_document", lambda app, doc: setattr(app, "ActiveDocument", doc)),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("tools.inks._page_inventory", lambda *args: inventory),
                            ("core.targets.resolve_page", lambda doc, index: index),
                            ("core.units.unit_name", lambda code: "in")):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"CORELDRAW_MCP_STATE_DIR": str(self.folder / "state")})
        env.start()
        self.addCleanup(env.stop)

    def test_dry_run_print_render_print_on_one_document(self):
        dry = printing.print_document("C:/jobs/x.cdr", "RIP", [1], True)
        self.assertTrue(dry.success, dry.error)
        self.assertNotIn("unsaved_changes", [warning["code"] for warning in dry.data["warnings"]])
        first = printing.print_document("C:/jobs/x.cdr", "RIP", [1], True, dry_run=False,
                                        submission_key="t-e-p1-f1-white", plates=["White"])
        self.assertTrue(first.success, first.error)
        self.assertFalse(self.doc.Dirty)
        render = plates.render_plates("C:/jobs/x.cdr", 1, str(self.folder / "expected-p1"),
                                      include_hidden_printable=True, dpi=36)
        self.assertTrue(render.success, render.error)
        self.assertFalse(self.doc.Dirty)
        self.assertEqual([plate["plate"] for plate in render.data["plates"]], ["White", "PANTONE 186 C"])
        # Each plate PNG records its resolution, so a film check knows its real size.
        with Image.open(render.data["plates"][0]["png"]) as png:
            self.assertEqual(tuple(round(value) for value in png.info["dpi"]), (36, 36))
        second = printing.print_document("C:/jobs/x.cdr", "RIP", [1], True, dry_run=False,
                                         submission_key="t-e-p1-f2-186", plates=["PANTONE 186 C"])
        self.assertTrue(second.success, second.error)
        self.assertEqual([[name for name, on in printed.items() if on] for printed in self.settings.printed],
                         [["White"], ["PANTONE 186 C"]])
        self.assertFalse(self.doc.Dirty)


if __name__ == "__main__":
    unittest.main()
