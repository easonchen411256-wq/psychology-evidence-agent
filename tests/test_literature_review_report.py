import json
import tempfile
import unittest
from pathlib import Path

from docx import Document

from psychology_evidence_agent.literature_review_report import (
    build_review_report,
    load_review_records,
    truncate,
)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class LiteratureReviewReportTests(unittest.TestCase):
    def create_search_directory(self, root: Path) -> None:
        write_json(
            root / "candidate_papers.json",
            [
                {
                    "paper_id": "W1",
                    "title": "Example paper",
                    "abstract": "This is an abstract about fear of falling and gait.",
                    "year": 2026,
                    "venue": "Example Journal",
                    "authors": ["Example Author"],
                    "doi": "https://doi.org/10.1000/example",
                }
            ],
        )
        screening = [
            {
                "paper_id": "W1",
                "relevance_score": 8,
                "evidence_level": "adjacent",
                "subtopic": "步态与跌倒恐惧",
                "rationale": "Relevant but not an intervention trial.",
                "human_review_note": "Check measures and study design.",
            }
        ]
        write_json(root / "screened_papers.json", screening)
        write_json(root / "priority_reading_list.json", screening)
        write_json(
            root / "search_report.json",
            {
                "topic": "Example topic",
                "research_question": "Example research question",
            },
        )
        write_json(
            root / "fulltext_access_candidates.json",
            {
                "candidates": [
                    {
                        "paper_id": "W1",
                        "access_status": "open_pdf_available",
                        "retrieval_source": "unpaywall",
                    }
                ]
            },
        )
        write_json(root / "manual_retrieval_queue.json", {"items": []})

    def test_joins_screening_and_candidate_metadata(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.create_search_directory(root)
            report, records, counts = load_review_records(root)
            self.assertEqual(report["research_question"], "Example research question")
            self.assertEqual(records[0]["title"], "Example paper")
            self.assertEqual(records[0]["relevance_score"], 8)
            self.assertEqual(counts["open_pdf_count"], 1)

    def test_creates_readable_word_report(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.create_search_directory(root)
            output = root / "report.docx"
            record_count, counts = build_review_report(root, output, abstract_limit=20)
            document = Document(output)
            paragraphs = "\n".join(paragraph.text for paragraph in document.paragraphs)
            self.assertEqual(record_count, 1)
            self.assertEqual(counts["selected_count"], 1)
            self.assertTrue(output.is_file())
            self.assertIn("文献人工审查报告", paragraphs)
            self.assertIn("Example paper", paragraphs)
            self.assertIn("摘要已截取前 20 个字符", paragraphs)

    def test_truncate_can_preserve_full_abstract(self):
        self.assertEqual(truncate("A long abstract", 0), "A long abstract")

    def test_derives_manual_queue_from_legacy_access_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.create_search_directory(root)
            (root / "manual_retrieval_queue.json").unlink()
            access_path = root / "fulltext_access_candidates.json"
            write_json(
                access_path,
                {
                    "candidates": [
                        {
                            "paper_id": "W1",
                            "access_status": "manual_access_needed",
                            "retrieval_source": "openalex",
                            "open_access_url": "https://doi.org/10.1000/example",
                        }
                    ]
                },
            )
            _report, records, counts = load_review_records(root)
            self.assertEqual(counts["manual_queue_count"], 1)
            self.assertIn("旧版全文清单", records[0]["manual_retrieval"]["reason"])


if __name__ == "__main__":
    unittest.main()
