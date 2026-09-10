import tempfile
import unittest
from pathlib import Path

from psychology_evidence_agent.batch_evidence_cards import (
    default_research_question,
    discover_paper_texts,
    evidence_card_path,
)


class BatchEvidenceCardTests(unittest.TestCase):
    def test_discovers_supported_files_recursively_in_stable_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "zeta.txt").write_text("text", encoding="utf-8")
            (root / "alpha.md").write_text("text", encoding="utf-8")
            (root / "ignore.pdf").write_text("pdf", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested" / "inner.md").write_text("text", encoding="utf-8")
            self.assertEqual(
                [path.name for path in discover_paper_texts(root)],
                ["alpha.md", "ignore.pdf", "inner.md", "zeta.txt"],
            )

    def test_builds_expected_card_path(self):
        self.assertEqual(
            evidence_card_path(Path("data/raw/paper_one.md"), Path("data/processed")),
            Path("data/processed/paper_one.evidence_card.json"),
        )

    def test_preserves_relative_subdirectory_in_card_path(self):
        self.assertEqual(
            evidence_card_path(
                Path("data/raw/open_access/paper_one.pdf"), Path("data/processed"), Path("data/raw")
            ),
            Path("data/processed/open_access/paper_one.evidence_card.json"),
        )

    def test_uses_configured_question_when_available(self):
        self.assertIn("HRV", default_research_question())


if __name__ == "__main__":
    unittest.main()
