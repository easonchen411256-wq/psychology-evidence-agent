import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from psychology_evidence_agent import run_config
from psychology_evidence_agent.run_literature_search import (
    CONFIG_ENV_VAR,
    load_search_config,
)


class ConfigurationTests(unittest.TestCase):
    def test_packaged_default_loads_without_a_repository_working_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            original_directory = Path.cwd()
            try:
                os.chdir(temporary_directory)
                default = load_search_config(None)
            finally:
                os.chdir(original_directory)
        self.assertIn("research_question", default)

    def test_explicit_config_takes_precedence_over_environment_config(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            explicit = root / "explicit.json"
            environment = root / "environment.json"
            template = {
                "topic": "topic",
                "research_question": "question",
                "publication_year_from": 2020,
                "queries": [{"id": "q", "query": "a valid query", "purpose": "purpose"}],
            }
            explicit.write_text(json.dumps({**template, "topic": "explicit"}), encoding="utf-8")
            environment.write_text(
                json.dumps({**template, "topic": "environment"}), encoding="utf-8"
            )
            with patch.dict("os.environ", {CONFIG_ENV_VAR: str(environment)}):
                self.assertEqual(load_search_config(explicit)["topic"], "explicit")
                self.assertEqual(load_search_config(None)["topic"], "environment")

    def test_external_example_config_parses(self):
        example = Path("configs/examples/literature_search.example.json")
        config = load_search_config(example)
        self.assertEqual(config["queries"][0]["id"], "replace_me")

    def test_invalid_config_is_rejected_before_search(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            invalid = Path(temporary_directory) / "invalid.json"
            invalid.write_text(
                json.dumps(
                    {
                        "topic": "topic",
                        "research_question": "question",
                        "publication_year_from": 2020,
                        "queries": [
                            {"id": "same", "query": "a valid query", "purpose": "one"},
                            {"id": "same", "query": "another query", "purpose": "two"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_search_config(invalid)

    def test_config_init_creates_parseable_file_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "editable.json"
            self.assertEqual(run_config.main(["init", "--output", str(output)]), 0)
            self.assertEqual(load_search_config(output)["queries"][0]["id"], "intervention_fof")
            original = output.read_bytes()
            self.assertEqual(run_config.main(["init", "--output", str(output)]), 2)
            self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
