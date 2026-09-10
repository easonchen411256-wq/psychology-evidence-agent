import tempfile
import unittest
from pathlib import Path

from psychology_evidence_agent.adapters.persistence.artifact_store import FileSystemArtifactStore
from psychology_evidence_agent.domain.agent import (
    AgentGoal,
    AgentObservation,
    ExecutionBudget,
    ToolDescriptor,
    ToolEffect,
)
from psychology_evidence_agent.domain.enums import (
    InferenceStrength,
    MaterialCompleteness,
    RunStage,
    RunStatus,
)
from psychology_evidence_agent.domain.errors import StructuredOutputError
from psychology_evidence_agent.domain.evidence import (
    ClaimBoundaries,
    EvidenceCard,
    EvidenceFinding,
    EvidenceSource,
    StudyMetadata,
)
from psychology_evidence_agent.domain.run import create_research_run
from psychology_evidence_agent.services.evidence_tools import (
    AdaptiveEvidenceFinalizeTool,
    AdaptiveEvidencePrepareTool,
    AdaptiveEvidenceProcessNextTool,
)
from psychology_evidence_agent.services.planning import DeterministicPlanner


class FakeReader:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def read_text(self, path: Path, *, max_chars: int, allow_large_input: bool) -> str:
        self.calls.append(path)
        assert max_chars == 160_000
        assert allow_large_input is False
        return "fixture paper material"


class FakeEvidenceExtraction:
    def __init__(self, card: EvidenceCard, *, fail: bool = False) -> None:
        self.card = card
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def extract(self, *, paper_text: str, research_question: str) -> EvidenceCard:
        self.calls.append((paper_text, research_question))
        if self.fail:
            raise StructuredOutputError("fixture schema failure")
        return self.card


class EvidenceToolTests(unittest.TestCase):
    def _card(self) -> EvidenceCard:
        return EvidenceCard(
            source=EvidenceSource(
                title="Fixture paper",
                authors=["Author"],
                year="2024",
                journal="Fixture Journal",
                doi_or_url="10.1234/fixture",
            ),
            material_completeness=MaterialCompleteness.COMPLETE,
            study=StudyMetadata(
                research_question="Question",
                design="randomized controlled trial",
                sample="100 adults",
                measures=["scale"],
                analysis="mixed model",
            ),
            findings=[
                EvidenceFinding(
                    finding="The fixture intervention was associated with the outcome.",
                    inference_strength=InferenceStrength.ASSOCIATION,
                    evidence_location="Results",
                )
            ],
            limitations=["Fixture limitation"],
            claim_boundaries=ClaimBoundaries(
                supported_claims=["The groups differed in the observed outcome."],
                unsupported_claims=["The intervention proves a universal causal effect."],
            ),
            human_review_items=[],
        )

    def _store(
        self, root: Path, *, logical_key: str = "documents/W1.pdf"
    ) -> FileSystemArtifactStore:
        store = FileSystemArtifactStore(root / "artifacts")
        document_path = root / "artifacts" / logical_key
        if ".." not in Path(logical_key).parts:
            document_path.parent.mkdir(parents=True, exist_ok=True)
            document_path.write_bytes(b"%PDF-1.7 fixture")
        store.save_json(
            "fulltext_documents.json",
            {
                "documents": [
                    {
                        "paper_id": "W1",
                        "title": "Fixture paper",
                        "logical_key": logical_key,
                        "retrieval_source": "fixture",
                    }
                ],
                "skipped_paper_ids": [],
            },
        )
        return store

    def test_extracts_validated_card_in_a_bounded_queue_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store(root)
            reader = FakeReader()
            extraction = FakeEvidenceExtraction(self._card())
            prepare = AdaptiveEvidencePrepareTool(store, root / "artifacts")
            process = AdaptiveEvidenceProcessNextTool(
                store,
                root / "artifacts",
                reader,
                extraction,
            )
            finalize = AdaptiveEvidenceFinalizeTool(store)
            run = create_research_run("Question")

            prepared = prepare.execute(run, {})
            first = process.execute(run, {"batch_size": 1})
            reused = process.execute(run, {"batch_size": 1})
            final = finalize.execute(run, {})

            self.assertTrue(prepared.success)
            self.assertTrue(first.success)
            self.assertTrue(reused.success)
            self.assertTrue(final.success)
            self.assertEqual(len(extraction.calls), 1)
            self.assertEqual(len(reader.calls), 1)
            self.assertTrue(store.load_json("evidence_cards/W1.json")["findings"])
            self.assertTrue(
                (root / "artifacts" / "agent" / "evidence_batches" / "batch_1.json").is_file()
            )

    def test_extraction_failure_is_persisted_without_calling_a_real_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store(root)
            prepare = AdaptiveEvidencePrepareTool(store, root / "artifacts")
            process = AdaptiveEvidenceProcessNextTool(
                store,
                root / "artifacts",
                FakeReader(),
                FakeEvidenceExtraction(self._card(), fail=True),
            )
            run = create_research_run("Question")
            prepare.execute(run, {})

            failed = process.execute(run, {})

            self.assertFalse(failed.success)
            self.assertFalse(failed.retryable)
            self.assertEqual(
                store.load_json("agent/evidence_queue.json")["items"][0]["status"], "failed"
            )
            self.assertEqual(
                store.load_json("workflow_checkpoints.json")["entries"]["extracting_evidence:W1"][
                    "retryable"
                ],
                False,
            )

    def test_prepare_rejects_document_path_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store(root, logical_key="../outside.pdf")

            result = AdaptiveEvidencePrepareTool(store, root / "artifacts").execute(
                create_research_run("Question"), {}
            )

            self.assertFalse(result.success)
            self.assertIn("unavailable", result.summary)

    def test_deterministic_planner_emits_bounded_evidence_steps(self):
        goal = AgentGoal(objective="Question", research_question="Question")
        observation = AgentObservation(
            goal_id=goal.goal_id,
            current_stage=RunStage.EXTRACTING_EVIDENCE,
            run_status=RunStatus.RUNNING,
            budget=ExecutionBudget(),
        )
        tools = [
            ToolDescriptor(
                name=name,
                description=name,
                stage=RunStage.EXTRACTING_EVIDENCE,
                effect=ToolEffect.LOCAL,
            )
            for name in ("evidence.prepare", "evidence.process_next", "evidence.finalize")
        ]

        plan = DeterministicPlanner().plan(goal, observation, tools)

        self.assertEqual(
            [step.tool_name for step in plan.steps],
            ["evidence.prepare", "evidence.process_next", "evidence.finalize"],
        )
        self.assertEqual(plan.steps[1].arguments, {"batch_size": 1})


if __name__ == "__main__":
    unittest.main()
