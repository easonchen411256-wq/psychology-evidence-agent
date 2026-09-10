import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from psychology_evidence_agent.open_access_fulltext import (
    classify_access,
    download_open_pdf,
    fetch_europe_pmc_options,
    fetch_unpaywall_options,
    manual_retrieval_item,
    normalized_doi,
    safe_pdf_filename,
    select_best_access_option,
    work_identifier,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        import json

        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakePdfResponse:
    headers = {"Content-Type": "application/pdf", "Content-Length": "9"}

    def geturl(self):
        return "https://example.org/paper.pdf"

    def read(self, _limit):
        return b"%PDF-1.7"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class OpenAccessFulltextTests(unittest.TestCase):
    def test_classifies_explicit_open_pdf(self):
        candidate = classify_access(
            {
                "paper_id": "https://openalex.org/W1",
                "title": "A paper",
                "is_open_access": True,
                "open_access_pdf_url": "https://example.org/a.pdf",
            }
        )
        self.assertEqual(candidate["access_status"], "open_pdf_available")

    def test_requires_manual_access_without_explicit_pdf(self):
        candidate = classify_access(
            {"paper_id": "https://openalex.org/W2", "title": "A paper", "is_open_access": False}
        )
        self.assertEqual(candidate["access_status"], "manual_access_needed")

    def test_makes_stable_filename_and_identifier(self):
        self.assertEqual(work_identifier("https://openalex.org/W42"), "W42")
        self.assertTrue(
            safe_pdf_filename(
                {"paper_id": "https://openalex.org/W42", "title": "A: paper"}
            ).endswith("_W42.pdf")
        )
        malicious = safe_pdf_filename({"paper_id": r"..\..\..\escaped", "title": "A paper"})
        self.assertEqual(Path(malicious).name, malicious)
        self.assertNotIn("\\", malicious)

    def test_normalizes_doi_before_lookup(self):
        self.assertEqual(normalized_doi("https://doi.org/10.1000/example."), "10.1000/example")

    def test_reads_unpaywall_pdf_location_when_email_is_supplied(self):
        paper = {
            "paper_id": "https://openalex.org/W3",
            "title": "A paper",
            "doi": "https://doi.org/10.1000/example",
        }
        response = {
            "best_oa_location": {
                "url_for_pdf": "https://repository.example/a.pdf",
                "url": "https://repository.example/a",
                "license": "cc-by",
                "host_type": "repository",
            },
            "oa_locations": [],
        }
        options = fetch_unpaywall_options(
            paper, "student@example.edu", opener=lambda request, timeout: FakeResponse(response)
        )
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["retrieval_source"], "unpaywall")
        self.assertEqual(options[0]["access_status"], "open_pdf_available")

    def test_reads_europe_pmc_open_pdf_location(self):
        paper = {
            "paper_id": "https://openalex.org/W4",
            "title": "A paper",
            "doi": "10.1000/example",
        }
        response = {
            "resultList": {
                "result": [
                    {"pmcid": "PMC123", "isOpenAccess": "Y", "hasPDF": "Y", "license": "CC BY"}
                ]
            }
        }
        options = fetch_europe_pmc_options(
            paper, opener=lambda request, timeout: FakeResponse(response)
        )
        self.assertEqual(
            options[0]["open_access_pdf_url"], "https://europepmc.org/articles/pmc123?pdf=render"
        )
        self.assertEqual(options[0]["access_status"], "open_pdf_available")

    def test_prefers_confirmed_pdf_to_open_landing_page(self):
        landing = {
            "retrieval_source": "openalex",
            "access_status": "open_landing_page_only",
            "open_access_pdf_url": "",
        }
        pdf = {
            "retrieval_source": "europe_pmc",
            "access_status": "open_pdf_available",
            "open_access_pdf_url": "https://example.org/a.pdf",
        }
        self.assertEqual(select_best_access_option([landing, pdf]), pdf)

    def test_builds_actionable_manual_queue_item(self):
        candidate = {
            "paper_id": "W5",
            "title": "A paper",
            "doi": "10.1000/example",
            "access_status": "manual_access_needed",
            "access_options": [
                {
                    "retrieval_source": "openalex",
                    "open_access_url": "https://doi.org/10.1000/example",
                }
            ],
        }
        item = manual_retrieval_item(candidate)
        self.assertEqual(item["checked_sources"], ["openalex"])
        self.assertIn("authorized", item["next_step"])

    def test_download_is_atomic_and_accepts_only_public_https_pdf(self):
        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            destination = download_open_pdf(
                {
                    "paper_id": "https://openalex.org/W6",
                    "title": "A paper",
                    "access_status": "open_pdf_available",
                    "open_access_pdf_url": "https://example.org/paper.pdf",
                },
                output_dir,
                opener=lambda request, timeout: FakePdfResponse(),
                resolver=lambda host, port, **kwargs: [(0, 0, 0, "", ("93.184.216.34", port))],
            )
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.read_bytes(), b"%PDF-1.7")

        with self.assertRaises(ValueError):
            download_open_pdf(
                {
                    "paper_id": "W7",
                    "access_status": "open_pdf_available",
                    "open_access_pdf_url": "https://127.0.0.1/paper.pdf",
                },
                Path(temporary_directory),
                opener=lambda request, timeout: self.fail("private URL was opened"),
            )


if __name__ == "__main__":
    unittest.main()
