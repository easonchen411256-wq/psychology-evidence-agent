import os
import tempfile
import unittest
from pathlib import Path

from psychology_evidence_agent.resources import (
    load_default_config,
    load_prompt,
    load_schema,
    schema_file,
    web_directory,
    web_resource,
)


class ResourceTests(unittest.TestCase):
    def test_runtime_resources_load_outside_the_repository_working_directory(self):
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary_directory:
            os.chdir(temporary_directory)
            try:
                self.assertIn("Evidence Extraction", load_prompt("evidence_extraction_prompt.md"))
                self.assertEqual(
                    load_schema("evidence_card.schema.json")["title"], "Psychology Evidence Card"
                )
                self.assertIn(
                    "research_question", load_default_config("default.literature_search.json")
                )
                self.assertTrue(web_resource("index.html").is_file())
                self.assertTrue((web_directory() / "index.html").is_file())
                self.assertTrue((web_directory() / "app.js").is_file())
                self.assertTrue((web_directory() / "app.css").is_file())
                with schema_file("evidence_card.schema.json") as path:
                    self.assertTrue(path.is_file())
            finally:
                os.chdir(original_directory)


if __name__ == "__main__":
    unittest.main()
