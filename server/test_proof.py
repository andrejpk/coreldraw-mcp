"""publish_proof (export_page_proof_pdf, render_plates) leaves the document as it found it (no CorelDRAW needed)."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import proof


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


if __name__ == "__main__":
    unittest.main()
