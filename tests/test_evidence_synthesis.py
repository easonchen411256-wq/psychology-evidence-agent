import json
import tempfile
import unittest
from pathlib import Path

from psychology_evidence_agent.evidence_synthesis import build_synthesis, write_synthesis_outputs


def make_card(completeness="complete"):
    return {
        "source": {"title": "Example paper", "year": "2026"},
        "material_completeness": completeness,
        "study": {
            "design": "randomized psychological intervention in older adults",
            "sample": "Older adults with fear of falling",
            "measures": ["HRV", "gait"],
        },
        "findings": [{"finding": "Reported outcome.", "inference_strength": "intervention_effect"}],
        "limitations": ["Small sample."],
        "claim_boundaries": {"supported_claims": ["A limited claim."], "unsupported_claims": []},
        "human_review_items": [] if completeness == "complete" else ["Check full text."],
    }


class EvidenceSynthesisTests(unittest.TestCase):
    def test_builds_conservative_candidates_and_skips_invalid_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            direct, partial, broken = (
                root / "direct.evidence_card.json",
                root / "partial.evidence_card.json",
                root / "broken.evidence_card.json",
            )
            direct.write_text(json.dumps(make_card()), encoding="utf-8")
            partial.write_text(json.dumps(make_card("partial")), encoding="utf-8")
            broken.write_text("not json", encoding="utf-8")
            synthesis = build_synthesis([direct, partial, broken])
            self.assertEqual(synthesis.summary.included_count, 2)
            self.assertEqual(synthesis.summary.skipped_count, 1)
            self.assertEqual(synthesis.summary.role_counts["review_required"], 2)

    def test_writes_json_csv_markdown_and_review_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            card_path = root / "one.evidence_card.json"
            card_path.write_text(json.dumps(make_card("partial")), encoding="utf-8")
            outputs = write_synthesis_outputs(build_synthesis([card_path]), root / "out")
            self.assertTrue(all(path.exists() for path in outputs.values()))
            self.assertIn("Example paper", outputs["markdown"].read_text(encoding="utf-8"))
            self.assertIn("Check full text.", outputs["review_queue"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
