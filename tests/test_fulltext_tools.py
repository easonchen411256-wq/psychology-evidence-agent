import tempfile
import unittest
from pathlib import Path

from psychology_evidence_agent.adapters.persistence.artifact_store import FileSystemArtifactStore
from psychology_evidence_agent.adapters.persistence.run_store import FileSystemResearchRunStore
from psychology_evidence_agent.domain.agent import (
    AgentGoal,
    AgentObservation,
    ExecutionBudget,
    ToolDescriptor,
    ToolEffect,
)
from psychology_evidence_agent.domain.enums import (
    ArtifactType,
    HumanDecisionType,
    OpenAccessStatus,
    RunStage,
    RunStatus,
)
from psychology_evidence_agent.domain.errors import ExternalServiceError
from psychology_evidence_agent.domain.fulltext import FullTextCandidate
from psychology_evidence_agent.domain.paper import Paper
from psychology_evidence_agent.domain.run import HumanDecision, create_research_run
from psychology_evidence_agent.runtime.human_gate import HumanGate
from psychology_evidence_agent.runtime.state_machine import RunStateMachine
from psychology_evidence_agent.services.fulltext_tools import (
    AdaptiveFullTextFinalizeTool,
    AdaptiveFullTextPrepareTool,
    AdaptiveFullTextProcessNextTool,
)
from psychology_evidence_agent.services.planning import DeterministicPlanner


class FakeFullText:
    def __init__(self, candidate: FullTextCandidate, *, fail_once: bool = False) -> None:
        self.candidate = candidate
        self.fail_once = fail_once
        self.calls = 0

    def discover_for_paper(self, *, paper: Paper, unpaywall_email: str) -> FullTextCandidate:
        del paper, unpaywall_email
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise ExternalServiceError("fixture provider unavailable")
        return self.candidate


class FakeDownloader:
    def __init__(self, *, outside_root: Path | None = None) -> None:
        self.outside_root = outside_root

    def download(self, candidate, output_dir: Path, *, overwrite: bool = False) -> Path:
        del candidate, overwrite
        target_dir = self.outside_root or output_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "paper.pdf"
        path.write_bytes(b"%PDF-1.7 fixture")
        return path


class FullTextToolTests(unittest.TestCase):
    def _paper(self) -> Paper:
        return Paper(paper_id="W1", title="Paper 1", doi="10.1234/example")

    def _candidate(self, *, status: OpenAccessStatus) -> FullTextCandidate:
        return FullTextCandidate(
            retrieval_source="fixture",
            paper_id="W1",
            title="Paper 1",
            doi="10.1234/example",
            is_open_access=status is not OpenAccessStatus.MANUAL_ACCESS_NEEDED,
            open_access_pdf_url="https://example.org/paper.pdf"
            if status is OpenAccessStatus.OPEN_PDF_AVAILABLE
            else "",
            open_access_url="https://example.org/article"
            if status is not OpenAccessStatus.MANUAL_ACCESS_NEEDED
            else "",
            access_status=status,
            next_step="fixture",
        )

    def _store_with_screening(self, root: Path) -> FileSystemArtifactStore:
        store = FileSystemArtifactStore(root / "artifacts")
        paper = self._paper()
        store.save_json(
            "screening_results.json",
            {
                "priority_papers": [paper.model_dump(mode="json")],
                "screened_papers": [],
            },
        )
        return store

    def _running_run(self, root: Path):
        run_store = FileSystemResearchRunStore(root)
        run = create_research_run("Question")
        run.stage = RunStage.RETRIEVING_FULLTEXT
        run_store.create(run)
        RunStateMachine().transition_status(run, RunStatus.RUNNING)
        run_store.save(run)
        return run_store, run

    def test_lawful_pdf_is_downloaded_and_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store_with_screening(root)
            candidate = self._candidate(status=OpenAccessStatus.OPEN_PDF_AVAILABLE)
            prepare = AdaptiveFullTextPrepareTool(store)
            process = AdaptiveFullTextProcessNextTool(
                store,
                FakeFullText(candidate),
                FakeDownloader(),
                root / "artifacts",
                HumanGate(RunStateMachine(), FileSystemResearchRunStore(root)),
            )
            finalize = AdaptiveFullTextFinalizeTool(store)
            run = create_research_run("Question")

            self.assertTrue(prepare.execute(run, {"batch_size": 1}).success)
            processed = process.execute(run, {"batch_size": 1})
            finalized = finalize.execute(run, {})

            self.assertTrue(processed.success)
            self.assertTrue(finalized.success)
            manifest = store.load_json("fulltext_documents.json")
            self.assertEqual(manifest["documents"][0]["paper_id"], "W1")
            self.assertTrue((root / "artifacts" / "documents" / "paper.pdf").is_file())
            self.assertTrue(
                (root / "artifacts" / "agent" / "fulltext_batches" / "batch_1.json").is_file()
            )

    def test_manual_access_creates_gate_and_provided_file_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store_with_screening(root)
            run_store, run = self._running_run(root)
            gate = HumanGate(RunStateMachine(), run_store)
            process = AdaptiveFullTextProcessNextTool(
                store,
                FakeFullText(self._candidate(status=OpenAccessStatus.MANUAL_ACCESS_NEEDED)),
                FakeDownloader(),
                root / "artifacts",
                gate,
            )
            prepare = AdaptiveFullTextPrepareTool(store)
            finalize = AdaptiveFullTextFinalizeTool(store)
            prepare.execute(run, {})

            blocked = process.execute(run, {})

            self.assertTrue(blocked.blocked)
            self.assertEqual(run.status, RunStatus.WAITING_FOR_HUMAN)
            action = next(item for item in run.human_actions if item.decision is None)
            provided_path = root / "artifacts" / "human" / "provided.pdf"
            provided_path.parent.mkdir(parents=True)
            provided_path.write_bytes(b"%PDF-1.7 provided")
            reference = store.reference("human/provided.pdf", ArtifactType.FULLTEXT_DOCUMENT)
            gate.resolve(
                run,
                HumanDecision(
                    action_id=action.action_id,
                    decision_type=HumanDecisionType.PROVIDE_FULLTEXT,
                    provided_artifact_reference=reference,
                ),
            )

            resumed = process.execute(run, {})
            finalized = finalize.execute(run, {})

            self.assertTrue(resumed.success)
            self.assertTrue(finalized.success)
            self.assertEqual(
                store.load_json("fulltext_documents.json")["documents"][0]["retrieval_source"],
                "human_provided",
            )

    def test_skip_decision_completes_queue_without_publishing_skipped_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store_with_screening(root)
            run_store, run = self._running_run(root)
            gate = HumanGate(RunStateMachine(), run_store)
            prepare = AdaptiveFullTextPrepareTool(store)
            process = AdaptiveFullTextProcessNextTool(
                store,
                FakeFullText(self._candidate(status=OpenAccessStatus.MANUAL_ACCESS_NEEDED)),
                FakeDownloader(),
                root / "artifacts",
                gate,
            )
            finalize = AdaptiveFullTextFinalizeTool(store)
            prepare.execute(run, {})
            process.execute(run, {})
            action = next(item for item in run.human_actions if item.decision is None)
            gate.resolve(
                run,
                HumanDecision(
                    action_id=action.action_id, decision_type=HumanDecisionType.SKIP_PAPER
                ),
            )

            resumed = process.execute(run, {})
            finalized = finalize.execute(run, {})

            self.assertTrue(resumed.success)
            self.assertFalse(finalized.success)
            self.assertIn("No full-text documents", finalized.summary)

    def test_provider_failure_is_retryable_and_does_not_call_real_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store_with_screening(root)
            fake = FakeFullText(
                self._candidate(status=OpenAccessStatus.OPEN_PDF_AVAILABLE), fail_once=True
            )
            process = AdaptiveFullTextProcessNextTool(
                store,
                fake,
                FakeDownloader(),
                root / "artifacts",
                HumanGate(RunStateMachine(), FileSystemResearchRunStore(root)),
            )
            prepare = AdaptiveFullTextPrepareTool(store)
            prepare.execute(create_research_run("Question"), {})
            run = create_research_run("Question")

            failed = process.execute(run, {})
            succeeded = process.execute(run, {})

            self.assertFalse(failed.success)
            self.assertTrue(failed.retryable)
            self.assertTrue(succeeded.success)
            self.assertEqual(fake.calls, 2)

    def test_deterministic_planner_emits_bounded_fulltext_steps(self):
        goal = AgentGoal(objective="Question", research_question="Question")
        observation = AgentObservation(
            goal_id=goal.goal_id,
            current_stage=RunStage.RETRIEVING_FULLTEXT,
            run_status=RunStatus.RUNNING,
            budget=ExecutionBudget(),
        )
        tools = [
            ToolDescriptor(
                name=name,
                description=name,
                stage=RunStage.RETRIEVING_FULLTEXT,
                effect=ToolEffect.LOCAL,
            )
            for name in ("fulltext.prepare", "fulltext.process_next", "fulltext.finalize")
        ]

        plan = DeterministicPlanner().plan(goal, observation, tools)

        self.assertEqual(
            [step.tool_name for step in plan.steps],
            ["fulltext.prepare", "fulltext.process_next", "fulltext.finalize"],
        )
        self.assertEqual(plan.steps[1].arguments, {"batch_size": 1})


if __name__ == "__main__":
    unittest.main()
