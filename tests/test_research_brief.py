import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from psychology_evidence_agent.adapters.persistence.artifact_store import FileSystemArtifactStore
from psychology_evidence_agent.adapters.persistence.run_store import FileSystemResearchRunStore
from psychology_evidence_agent.agent_cli import BUDGET_ARTIFACT, _show_status, parse_args
from psychology_evidence_agent.domain.agent import (
    AgentGoal,
    ExecutionBudget,
    ResearchBrief,
    SearchPreferences,
)
from psychology_evidence_agent.domain.run import create_research_run


class ResearchBriefTests(unittest.TestCase):
    def test_goal_gets_backward_compatible_default_brief(self):
        goal = AgentGoal(objective="Find evidence", research_question="What works?")

        self.assertIsNotNone(goal.brief)
        self.assertEqual(goal.brief.research_question, "What works?")

    def test_brief_preserves_structured_scope(self):
        brief = ResearchBrief(
            research_question="What works?",
            population="Older adults",
            intervention_or_exposure="Psychological intervention",
            outcomes=["fear of falling", "HRV"],
            inclusion_criteria=["Peer-reviewed"],
            exclusion_criteria=["Animal studies"],
            year_from=2015,
            year_to=2026,
        )
        restored = ResearchBrief.model_validate(brief.model_dump(mode="json"))

        self.assertEqual(restored, brief)

    def test_brief_rejects_invalid_year_range(self):
        with self.assertRaises(ValidationError):
            ResearchBrief(research_question="Question", year_from=2026, year_to=2015)

    def test_goal_rejects_mismatched_brief_question(self):
        with self.assertRaises(ValidationError):
            AgentGoal(
                objective="Find evidence",
                research_question="Question A",
                brief=ResearchBrief(research_question="Question B"),
            )

    def test_manual_search_preferences_require_and_preserve_a_query(self):
        goal = AgentGoal(
            objective="Find evidence",
            research_question="What works?",
            search_preferences=SearchPreferences(
                mode="manual", manual_query=" older adults AND intervention ", candidate_limit=12
            ),
        )

        self.assertEqual(goal.search_preferences.manual_query, "older adults AND intervention")
        self.assertEqual(goal.search_preferences.candidate_limit, 12)
        with self.assertRaises(ValidationError):
            SearchPreferences(mode="manual")

    def test_cli_parses_structured_research_scope(self):
        args = parse_args(
            [
                "run",
                "Study an intervention",
                "--research-question",
                "What changes?",
                "--population",
                "Older adults",
                "--outcome",
                "HRV",
                "--outcome",
                "Gait",
                "--include",
                "Peer-reviewed",
                "--year-from",
                "2015",
                "--year-to",
                "2026",
            ]
        )

        self.assertEqual(args.research_question, "What changes?")
        self.assertEqual(args.outcome, ["HRV", "Gait"])
        self.assertEqual(args.inclusion_criteria, ["Peer-reviewed"])
        self.assertEqual(args.year_from, 2015)

    def test_status_serializes_persisted_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = create_research_run("Question")
            FileSystemResearchRunStore(root).create(run)
            artifacts = FileSystemArtifactStore(root / "runs" / run.run_id / "artifacts")
            artifacts.save_json(BUDGET_ARTIFACT, ExecutionBudget().model_dump(mode="json"))

            output = io.StringIO()
            with redirect_stdout(output):
                result = _show_status(SimpleNamespace(store_root=root, run_id=run.run_id))

            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["budget"]["max_steps"], 40)


if __name__ == "__main__":
    unittest.main()
