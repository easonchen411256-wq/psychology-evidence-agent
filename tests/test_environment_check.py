import tempfile
import unittest
from pathlib import Path

from psychology_evidence_agent.environment_check import package_version, sha256_file


class EnvironmentCheckTests(unittest.TestCase):
    def test_sha256_file_is_stable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "config.json"
            path.write_text('{"topic":"example"}', encoding="utf-8")
            self.assertEqual(
                sha256_file(path),
                "2b6cd4bcfc8257dbba8402630115f49907bc3ca05dd5aae8cad67f048ee875f9",
            )

    def test_missing_package_is_reported_without_crashing(self):
        self.assertEqual(
            package_version("package-that-does-not-exist-for-this-test"), "not installed"
        )


if __name__ == "__main__":
    unittest.main()
