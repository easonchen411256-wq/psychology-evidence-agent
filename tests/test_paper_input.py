import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from psychology_evidence_agent.paper_input import PaperInputError, read_paper_text


class PaperInputTests(unittest.TestCase):
    def test_reads_utf8_markdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "paper.md"
            path.write_text("# Paper\n\nBody", encoding="utf-8")
            self.assertEqual(read_paper_text(path, max_chars=100), "# Paper\n\nBody")

    def test_accepts_pdf_through_local_extractor(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "paper.pdf"
            path.write_bytes(b"placeholder")
            with patch(
                "psychology_evidence_agent.paper_input.extract_pdf_text",
                return_value="Extracted PDF text",
            ):
                self.assertEqual(read_paper_text(path, max_chars=100), "Extracted PDF text")

    def test_preserves_page_markers_when_extracting_pdf_text(self):
        class Page:
            def __init__(self, value):
                self.value = value

            def extract_text(self):
                return self.value

        class Reader:
            is_encrypted = False
            pages = [Page("First page"), Page("Second page")]

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "paper.pdf"
            path.write_bytes(b"placeholder")
            with patch("psychology_evidence_agent.paper_input.PdfReader", return_value=Reader()):
                text = read_paper_text(path, max_chars=100)
            self.assertIn("【第 1 页】\nFirst page", text)
            self.assertIn("【第 2 页】\nSecond page", text)

    def test_rejects_image_only_pdf(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with path.open("wb") as handle:
                writer.write(handle)
            with self.assertRaisesRegex(PaperInputError, "No searchable text"):
                read_paper_text(path, max_chars=100)


if __name__ == "__main__":
    unittest.main()
