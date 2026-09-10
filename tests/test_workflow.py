import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from psychology_evidence_agent.adapters.persistence.artifact_store import FileSystemArtifactStore
from psychology_evidence_agent.adapters.persistence.event_store import FileSystemAgentEventStore
from psychology_evidence_agent.adapters.persistence.run_store import FileSystemResearchRunStore
from psychology_evidence_agent.domain.agent import AgentGoal, ExecutionBudget
from psychology_evidence_agent.domain.enums import (
    ArtifactType,
    HumanDecisionType,
    OpenAccessStatus,
    RunStage,
    RunStatus,
)
from psychology_evidence_agent.domain.evidence import EvidenceCard
from psychology_evidence_agent.domain.fulltext import FullTextCandidate
from psychology_evidence_agent.domain.paper import Paper
from psychology_evidence_agent.domain.review import (
    ClaimEvidence,
    DraftClaim,
    ReviewDraft,
    ReviewSection,
)
from psychology_evidence_agent.domain.run import HumanDecision, create_research_run
from psychology_evidence_agent.runtime.agent_controller import AgentController
from psychology_evidence_agent.runtime.human_gate import HumanGate
from psychology_evidence_agent.runtime.orchestrator import EvidenceAgent
from psychology_evidence_agent.runtime.state_machine import RunStateMachine
from psychology_evidence_agent.runtime.step_executor import StepExecutor
from psychology_evidence_agent.services.agent_tools import (
    AdaptiveEvidenceFinalizeTool,
    AdaptiveEvidencePrepareTool,
    AdaptiveEvidenceProcessNextTool,
    AdaptiveFullTextFinalizeTool,
    AdaptiveFullTextPrepareTool,
    AdaptiveFullTextProcessNextTool,
    AdaptiveScreeningExecuteTool,
    AdaptiveScreeningFinalizeTool,
    AdaptiveSearchCoverageTool,
    AdaptiveSearchFinalizeTool,
    AdaptiveSearchQueryTool,
    WorkflowToolRegistry,
)
from psychology_evidence_agent.services.evidence_extraction import EvidenceExtractionService
from psychology_evidence_agent.services.fulltext import FullTextService
from psychology_evidence_agent.services.literature_search import LiteratureSearchService
from psychology_evidence_agent.services.planning import DeterministicPlanner
from psychology_evidence_agent.services.review_draft import ReviewDraftService
from psychology_evidence_agent.services.screening import ScreeningService
from psychology_evidence_agent.services.workflow import ResearchWorkflow, WorkflowSettings


class FakeSearch:
    def search(self, *, query_id: str, query: str, year_from: int, per_page: int) -> list[Paper]:
        return [
            Paper(
                paper_id="W-offline",
                title="Offline evidence paper",
                abstract="A searchable abstract.",
                year=2024,
                matched_queries=[query_id],
                query_coverage=1,
                cited_by_count=3,
            )
        ]


class FakeStructuredOutput:
    def __init__(self, *, review_required: bool = False) -> None:
        self.review_required = review_required

    def generate(self, **kwargs: Any) -> dict[str, Any]:
        if "screening" in str(kwargs["schema_path"]):
            return {
                "screened_papers": [
                    {
                        "paper_id": "W-offline",
                        "relevance_score": 9,
                        "evidence_level": "direct",
                        "subtopic": "monitoring",
                        "rationale": "Relevant",
                        "human_review_note": (
                            "Abstract-level uncertainty requires human review."
                            if self.review_required
                            else ""
                        ),
                    }
                ]
            }
        return {}


class FakeFullTextDiscovery:
    def __init__(self, *, manual: bool) -> None:
        self.manual = manual

    def discover_for_paper(self, *, paper: Paper, unpaywall_email: str) -> FullTextCandidate:
        return FullTextCandidate(
            retrieval_source="fake",
            paper_id=paper.paper_id,
            title=paper.title,
            year=paper.year,
            is_open_access=not self.manual,
            open_access_pdf_url="https://example.org/paper.pdf" if not self.manual else "",
            access_status=(
                OpenAccessStatus.MANUAL_ACCESS_NEEDED
                if self.manual
                else OpenAccessStatus.OPEN_PDF_AVAILABLE
            ),
            next_step="manual" if self.manual else "download",
        )

    def discover(self, *, paper_id: str, unpaywall_email: str) -> FullTextCandidate:
        raise AssertionError("workflow should use metadata already loaded from search")


class FakeDownloader:
    def download(
        self, candidate: FullTextCandidate, output_dir: Path, *, overwrite: bool = False
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "offline.md"
        if path.exists() and not overwrite:
            return path
        path.write_text("Full text supplied by an offline test.", encoding="utf-8")
        return path


class FakeReader:
    def read_text(self, path: Path, *, max_chars: int, allow_large_input: bool) -> str:
        return path.read_text(encoding="utf-8")


def evidence_card() -> EvidenceCard:
    return EvidenceCard.model_validate(
        {
            "source": {
                "title": "Offline evidence paper",
                "authors": ["Test Author"],
                "year": "2024",
                "journal": "Test Journal",
                "doi_or_url": "https://doi.org/10.1000/offline",
            },
            "material_completeness": "complete",
            "study": {
                "research_question": "Question",
                "design": "observational",
                "sample": "older adults",
                "measures": ["gait"],
                "analysis": "descriptive",
            },
            "findings": [
                {
                    "finding": "The indicator was associated with the outcome.",
                    "inference_strength": "association",
                    "evidence_location": "Results",
                }
            ],
            "limitations": ["Offline fixture"],
            "claim_boundaries": {
                "supported_claims": ["Association only"],
                "unsupported_claims": ["Causality"],
            },
            "human_review_items": [],
        }
    )


class FakeEvidence:
    calls = 0

    def generate(self, **kwargs: Any) -> dict[str, Any]:
        FakeEvidence.calls += 1
        return evidence_card().model_dump(mode="json")


class FakeDraft:
    def generate(self, **kwargs: Any) -> dict[str, Any]:
        evidence = ClaimEvidence(
            evidence_card_file="evidence_cards/W-offline.json",
            finding="The indicator was associated with the outcome.",
            evidence_location="Results",
        )
        sections = [
            ReviewSection(
                heading=heading,
                paragraph="Cautious summary.",
                supporting_card_files=["evidence_cards/W-offline.json"] if index == 0 else [],
                claims=[DraftClaim(claim="Association was reported.", evidence=[evidence])]
                if index == 0
                else [],
                caveat="This does not establish causality.",
            )
            for index, heading in enumerate(("Body", "Emotion", "Cognition"))
        ]
        return ReviewDraft(
            title="Offline draft",
            research_question="Offline research question",
            evidence_scope="Offline fixture",
            sections=sections,
            evidence_gaps=[],
            human_review_items=[],
        ).model_dump(mode="json")


class AdaptiveSearchFixture:
    """Return a new paper on each bounded query round without using a network."""

    def search(self, *, query_id: str, query: str, year_from: int, per_page: int) -> list[Paper]:
        del query, year_from, per_page
        # Initializing is itself the first persisted Agent plan, so the first
        # search plan begins at revision two.
        paper_id = "W1" if query_id.endswith("_2") else "W2"
        return [
            Paper(
                paper_id=paper_id,
                title=f"Offline evidence paper {paper_id}",
                abstract="A searchable abstract.",
                year=2024,
                matched_queries=[query_id],
                query_coverage=1,
                cited_by_count=3,
            )
        ]


class MixedAccessFullTextFixture:
    """Keep one document automatic and require the existing Human Gate for the other."""

    def discover_for_paper(self, *, paper: Paper, unpaywall_email: str) -> FullTextCandidate:
        del unpaywall_email
        manual = paper.paper_id == "W2"
        return FullTextCandidate(
            retrieval_source="fixture",
            paper_id=paper.paper_id,
            title=paper.title,
            year=paper.year,
            is_open_access=not manual,
            open_access_pdf_url="https://example.org/paper.pdf" if not manual else "",
            access_status=(
                OpenAccessStatus.MANUAL_ACCESS_NEEDED
                if manual
                else OpenAccessStatus.OPEN_PDF_AVAILABLE
            ),
            next_step="manual" if manual else "download",
        )


class AdaptiveScreeningFixture:
    def generate(self, **kwargs: Any) -> dict[str, Any]:
        candidates = json.loads(str(kwargs["stdin_payload"]).rsplit("\n", 1)[-1])
        return {
            "screened_papers": [
                {
                    "paper_id": candidate["paper_id"],
                    "relevance_score": 9,
                    "evidence_level": "direct",
                    "subtopic": "monitoring",
                    "rationale": "Relevant offline fixture.",
                    "human_review_note": "",
                }
                for candidate in candidates
            ]
        }


class PerPaperDownloader:
    def download(
        self, candidate: FullTextCandidate, output_dir: Path, *, overwrite: bool = False
    ) -> Path:
        del overwrite
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{candidate.paper_id}.md"
        path.write_text(f"Fixture full text for {candidate.paper_id}.", encoding="utf-8")
        return path


class AdaptiveDraftFixture:
    def generate(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        evidence = ClaimEvidence(
            evidence_card_file="evidence_cards/W1.json",
            finding="The indicator was associated with the outcome.",
            evidence_location="Results",
        )
        return ReviewDraft(
            title="Offline Agent draft",
            research_question="Offline research question",
            evidence_scope="Offline fixture",
            sections=[
                ReviewSection(
                    heading=heading,
                    paragraph="Cautious summary.",
                    supporting_card_files=["evidence_cards/W1.json"] if index == 0 else [],
                    claims=[DraftClaim(claim="Association was reported.", evidence=[evidence])]
                    if index == 0
                    else [],
                    caveat="This does not establish causality.",
                )
                for index, heading in enumerate(("Body", "Emotion", "Cognition"))
            ],
            evidence_gaps=[],
            human_review_items=[],
        ).model_dump(mode="json")


class WorkflowEndToEndTests(unittest.TestCase):
    def test_agent_controller_completes_adaptive_lifecycle_after_human_resume(self):
        """Exercise the real controller, typed queues, re-plans, and restart recovery offline."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = create_research_run("Offline research question")
            run_store = FileSystemResearchRunStore(root)
            run_store.create(run)
            artifact_root = root / "runs" / run.run_id / "artifacts"
            artifact_store = FileSystemArtifactStore(artifact_root)
            state_machine = RunStateMachine()
            literature_search = LiteratureSearchService(AdaptiveSearchFixture())
            screening = ScreeningService(AdaptiveScreeningFixture())
            full_text = FullTextService(MixedAccessFullTextFixture())
            downloader = PerPaperDownloader()
            reader = FakeReader()
            evidence_extraction = EvidenceExtractionService(FakeEvidence())
            workflow = ResearchWorkflow(
                artifact_store=artifact_store,
                synthesis_store=FileSystemArtifactStore(artifact_root / "evidence_synthesis"),
                artifact_root=artifact_root,
                literature_search=literature_search,
                screening=screening,
                full_text=full_text,
                pdf_downloader=downloader,
                document_reader=reader,
                evidence_extraction=evidence_extraction,
                review_draft=ReviewDraftService(AdaptiveDraftFixture()),
                human_gate=HumanGate(state_machine, run_store),
                settings=WorkflowSettings(max_per_query=2, max_screen=2, screen_batch_size=1),
            )
            goal = AgentGoal(
                objective="Offline research question", research_question=run.research_question
            )
            event_path = root / "runs" / run.run_id / "events.jsonl"

            controller = self._adaptive_controller(
                run_store,
                artifact_store,
                event_path,
                workflow,
                state_machine,
                artifact_root,
                literature_search,
                screening,
                full_text,
                downloader,
                reader,
                evidence_extraction,
            )
            controller.run_until_blocked(run, goal)

            self.assertEqual(
                run.status,
                RunStatus.WAITING_FOR_HUMAN,
                f"Unexpected run failure: {run.failure}",
            )
            self.assertEqual(run.stage, RunStage.RETRIEVING_FULLTEXT)
            action = next(item for item in run.human_actions if item.decision is None)
            artifact_store.save_text("human/W2.md", "Human-provided lawful full text.")
            HumanGate(state_machine, run_store).resolve(
                run,
                HumanDecision(
                    action_id=action.action_id,
                    decision_type=HumanDecisionType.PROVIDE_FULLTEXT,
                    provided_artifact_reference=artifact_store.reference(
                        "human/W2.md", ArtifactType.FULLTEXT_DOCUMENT
                    ),
                ),
            )

            restarted = self._adaptive_controller(
                run_store,
                artifact_store,
                event_path,
                workflow,
                state_machine,
                artifact_root,
                literature_search,
                screening,
                full_text,
                downloader,
                reader,
                evidence_extraction,
            )
            restarted.run_until_blocked(run, goal)

            self.assertEqual(
                run.status,
                RunStatus.COMPLETED,
                f"Unexpected run failure after resume: {run.failure}",
            )
            self.assertEqual(run.stage, RunStage.DRAFTING)
            self.assertTrue((artifact_root / "review_draft" / "review_draft.json").is_file())
            self.assertTrue((artifact_root / "evidence_cards" / "W1.json").is_file())
            self.assertTrue((artifact_root / "evidence_cards" / "W2.json").is_file())

            events = FileSystemAgentEventStore(event_path).read()
            self.assertGreaterEqual(
                sum(event.event_type.value == "replan_started" for event in events), 4
            )
            self.assertTrue(
                any(event.event_type.value == "human_action_requested" for event in events)
            )
            self.assertFalse(any("Fixture full text" in event.summary for event in events))

    @staticmethod
    def _adaptive_controller(
        run_store: FileSystemResearchRunStore,
        artifact_store: FileSystemArtifactStore,
        event_path: Path,
        workflow: ResearchWorkflow,
        state_machine: RunStateMachine,
        artifact_root: Path,
        literature_search: LiteratureSearchService,
        screening: ScreeningService,
        full_text: FullTextService,
        downloader: PerPaperDownloader,
        reader: FakeReader,
        evidence_extraction: EvidenceExtractionService,
    ) -> AgentController:
        gate = HumanGate(state_machine, run_store)
        registry = WorkflowToolRegistry(
            workflow.handlers(),
            extra_tools=(
                AdaptiveSearchQueryTool(artifact_store, literature_search),
                AdaptiveSearchFinalizeTool(artifact_store),
                AdaptiveSearchCoverageTool(artifact_store, candidate_target=2),
                AdaptiveScreeningExecuteTool(artifact_store, screening),
                AdaptiveScreeningFinalizeTool(artifact_store, gate),
                AdaptiveFullTextPrepareTool(artifact_store),
                AdaptiveFullTextProcessNextTool(
                    artifact_store,
                    full_text,
                    downloader,
                    artifact_root,
                    gate,
                ),
                AdaptiveFullTextFinalizeTool(artifact_store),
                AdaptiveEvidencePrepareTool(artifact_store, artifact_root),
                AdaptiveEvidenceProcessNextTool(
                    artifact_store,
                    artifact_root,
                    reader,
                    evidence_extraction,
                ),
                AdaptiveEvidenceFinalizeTool(artifact_store),
            ),
        )
        return AgentController(
            run_store=run_store,
            artifact_store=artifact_store,
            event_store=FileSystemAgentEventStore(event_path),
            state_machine=state_machine,
            planner=DeterministicPlanner(),
            registry=registry,
            budget=ExecutionBudget(max_steps=40, max_replans=10, max_model_calls=10),
        )

    def test_real_service_wiring_completes_offline_without_network_or_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = create_research_run("Offline research question")
            agent, run_store, _workflow = self._build_for_run(root, run, manual=False)
            run_store.create(run)

            agent.run_until_blocked(run)

            self.assertEqual(run.status, RunStatus.COMPLETED)
            self.assertEqual(run.stage, RunStage.DRAFTING)
            self.assertTrue(
                (
                    root / "runs" / run.run_id / "artifacts" / "review_draft" / "review_draft.json"
                ).is_file()
            )

            checkpoints = FileSystemArtifactStore(
                root / "runs" / run.run_id / "artifacts"
            ).load_json("workflow_checkpoints.json")
            entries = checkpoints["entries"]
            self.assertTrue(any(key.startswith("searching:") for key in entries))
            self.assertTrue(any(key.startswith("screening:") for key in entries))
            self.assertEqual(entries["extracting_evidence:W-offline"]["status"], "completed")

    def test_evidence_checkpoint_reuses_matching_input_and_invalidates_changed_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = create_research_run("Offline research question")
            agent, run_store, workflow = self._build_for_run(root, run, manual=False)
            run_store.create(run)
            FakeEvidence.calls = 0
            agent.run_until_blocked(run)
            self.assertEqual(FakeEvidence.calls, 1)

            run.status = RunStatus.RUNNING
            run.stage = RunStage.EXTRACTING_EVIDENCE
            workflow.handlers()[RunStage.EXTRACTING_EVIDENCE](run)
            self.assertEqual(FakeEvidence.calls, 1)

            document = root / "runs" / run.run_id / "artifacts" / "documents" / "offline.md"
            document.write_text("Changed offline full text.", encoding="utf-8")
            workflow.handlers()[RunStage.EXTRACTING_EVIDENCE](run)
            self.assertEqual(FakeEvidence.calls, 2)

    def test_manual_fulltext_gate_can_be_resolved_and_resumed_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = create_research_run("Offline research question")
            agent, run_store, _workflow = self._build_for_run(root, run, manual=True)
            run_store.create(run)

            agent.run_until_blocked(run)

            self.assertEqual(run.status, RunStatus.WAITING_FOR_HUMAN)
            action = next(item for item in run.human_actions if item.decision is None)
            artifact_store = FileSystemArtifactStore(root / "runs" / run.run_id / "artifacts")
            logical_key = "documents/manual.md"
            artifact_store.save_text(logical_key, "Human-provided lawful full text.")
            reference = artifact_store.reference(logical_key, ArtifactType.FULLTEXT_DOCUMENT)
            HumanGate(RunStateMachine(), run_store).resolve(
                run,
                HumanDecision(
                    action_id=action.action_id,
                    decision_type=HumanDecisionType.PROVIDE_FULLTEXT,
                    provided_artifact_reference=reference,
                ),
            )
            agent.run_until_blocked(run)

            self.assertEqual(run.status, RunStatus.COMPLETED)
            self.assertEqual(run.stage, RunStage.DRAFTING)

    def _build_for_run(
        self, root: Path, run: Any, *, manual: bool, review_required: bool = False
    ) -> tuple[EvidenceAgent, FileSystemResearchRunStore, ResearchWorkflow]:
        run_store = FileSystemResearchRunStore(root)
        state_machine = RunStateMachine()
        artifact_root = root / "runs" / run.run_id / "artifacts"
        workflow = ResearchWorkflow(
            artifact_store=FileSystemArtifactStore(artifact_root),
            synthesis_store=FileSystemArtifactStore(artifact_root / "evidence_synthesis"),
            artifact_root=artifact_root,
            literature_search=LiteratureSearchService(FakeSearch()),
            screening=ScreeningService(FakeStructuredOutput(review_required=review_required)),
            full_text=FullTextService(FakeFullTextDiscovery(manual=manual)),
            pdf_downloader=FakeDownloader(),
            document_reader=FakeReader(),
            evidence_extraction=EvidenceExtractionService(FakeEvidence()),
            review_draft=ReviewDraftService(FakeDraft()),
            human_gate=HumanGate(state_machine, run_store),
            settings=WorkflowSettings(max_per_query=1, max_screen=1),
        )
        return (
            EvidenceAgent(
                run_store=run_store,
                state_machine=state_machine,
                step_executor=StepExecutor(workflow.handlers()),
            ),
            run_store,
            workflow,
        )

    def test_screening_review_gate_can_be_resolved_before_fulltext(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = create_research_run("Offline research question")
            agent, run_store, _workflow = self._build_for_run(
                root, run, manual=False, review_required=True
            )
            run_store.create(run)

            agent.run_until_blocked(run)

            self.assertEqual(run.status, RunStatus.WAITING_FOR_HUMAN)
            self.assertEqual(run.stage, RunStage.SCREENING)
            action = next(item for item in run.human_actions if item.decision is None)
            HumanGate(RunStateMachine(), run_store).resolve(
                run,
                HumanDecision(
                    action_id=action.action_id,
                    decision_type=HumanDecisionType.INCLUDE,
                ),
            )
            agent.run_until_blocked(run)

            self.assertEqual(run.status, RunStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
