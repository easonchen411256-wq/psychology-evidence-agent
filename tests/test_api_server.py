import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from psychology_evidence_agent import api_server
from psychology_evidence_agent.adapters.persistence.artifact_store import FileSystemArtifactStore
from psychology_evidence_agent.domain.agent import (
    AgentEventType,
    AgentGoal,
    ExecutionBudget,
    ResearchBrief,
)
from psychology_evidence_agent.domain.enums import HumanActionType, RunStage, RunStatus
from psychology_evidence_agent.domain.paper import Paper
from psychology_evidence_agent.domain.run import create_research_run
from psychology_evidence_agent.runtime.agent_controller import (
    BRIEF_ARTIFACT,
    BUDGET_ARTIFACT,
    GOAL_ARTIFACT,
)
from psychology_evidence_agent.runtime.state_machine import RunStateMachine
from psychology_evidence_agent.services.research_intake import ResearchIntakeResult


class ApiServerTests(unittest.TestCase):
    def setUp(self):
        self.previous_key = os.environ.pop("PEA_API_KEY", None)
        self.previous_root = os.environ.get("PEA_STORE_ROOT")
        self.temp_root = tempfile.TemporaryDirectory()
        os.environ["PEA_STORE_ROOT"] = self.temp_root.name
        self.client = TestClient(api_server.app)

    def tearDown(self):
        with api_server._RUN_MODEL_CONNECTIONS_LOCK:
            api_server._RUN_MODEL_CONNECTIONS.clear()
        self.temp_root.cleanup()
        if self.previous_root is None:
            os.environ.pop("PEA_STORE_ROOT", None)
        else:
            os.environ["PEA_STORE_ROOT"] = self.previous_root
        if self.previous_key is not None:
            os.environ["PEA_API_KEY"] = self.previous_key

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_system_capabilities_never_expose_the_configured_key(self):
        os.environ["PEA_API_KEY"] = "secret-key"
        response = self.client.get("/v1/system/capabilities")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["api_key_required"])
        self.assertNotIn("secret-key", response.text)
        self.assertIn("openai", [item["id"] for item in response.json()["model_providers"]])

    def test_external_model_connection_rejects_non_secure_remote_url(self):
        response = self.client.post(
            "/v1/research-intake/messages",
            json={
                "message": "研究心理干预",
                "model_connection": {
                    "provider": "custom",
                    "model": "test-model",
                    "api_base": "http://models.example/v1",
                    "api_key": "secret",
                },
            },
        )

        self.assertEqual(response.status_code, 422)

    @patch("psychology_evidence_agent.api_server.clarify_research_question")
    def test_research_intake_returns_structured_candidate_brief(self, mock_clarify):
        mock_clarify.return_value = ResearchIntakeResult(
            assistant_message="你更想关注哪类心理干预？",
            candidate_brief=ResearchBrief(
                research_question="老年人心理干预对跌倒恐惧的影响是什么？",
                population="老年人",
                intervention_or_exposure="心理干预",
                outcomes=["跌倒恐惧"],
            ),
            missing_information=["干预类型"],
            ready_to_run=False,
        )
        response = self.client.post(
            "/v1/research-intake/messages",
            json={"message": "我想研究老年人跌倒恐惧和心理干预。"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["candidate_brief"]["population"], "老年人")
        self.assertFalse(response.json()["ready_to_run"])
        mock_clarify.assert_called_once()

    @patch("psychology_evidence_agent.api_server.structured_output_adapter")
    @patch("psychology_evidence_agent.api_server.clarify_research_question")
    def test_research_intake_uses_selected_external_model(self, mock_clarify, mock_adapter):
        mock_clarify.return_value = ResearchIntakeResult(
            assistant_message="问题已整理。",
            candidate_brief=ResearchBrief(research_question="心理干预效果如何？"),
            ready_to_run=True,
        )
        response = self.client.post(
            "/v1/research-intake/messages",
            json={
                "message": "研究心理干预",
                "model_connection": {
                    "provider": "custom",
                    "model": "test-model",
                    "api_base": "https://models.example/v1",
                    "api_key": "secret",
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        connection = mock_adapter.call_args.args[0]
        self.assertEqual(connection.provider, "custom")
        self.assertEqual(connection.api_key, "secret")
        self.assertIs(mock_clarify.call_args.kwargs["structured_output"], mock_adapter.return_value)

    def _save_screening_artifacts(self, run_id: str) -> FileSystemArtifactStore:
        artifacts = FileSystemArtifactStore(
            Path(self.temp_root.name) / "runs" / run_id / "artifacts"
        )
        artifacts.save_json(
            "search_results.json",
            {
                "papers": [
                    {
                        "paper_id": "paper-1",
                        "title": "Mindfulness and fear of falling",
                        "year": 2022,
                        "venue": "Journal of Aging Psychology",
                        "authors": ["A. Researcher"],
                        "abstract": "A study abstract.",
                        "openalex_url": "https://openalex.org/W1",
                    }
                ]
            },
        )
        artifacts.save_json(
            "screening_results.json",
            {
                "screened_papers": [
                    {
                        "paper_id": "paper-1",
                        "relevance_score": 9,
                        "evidence_level": "direct",
                        "rationale": "The population and intervention match.",
                        "subtopic": "mindfulness",
                    }
                ]
            },
        )
        return artifacts

    def test_history_and_screening_results_expose_reviewable_counts(self):
        root = Path(self.temp_root.name)
        run = create_research_run("Review a psychology intervention")
        RunStateMachine().transition_status(run, RunStatus.RUNNING)
        api_server.research_run_store(root).create(run)
        self._save_screening_artifacts(run.run_id)

        history = self.client.get("/v1/agent/runs")
        screening = self.client.get(f"/v1/agent/runs/{run.run_id}/screening-results")

        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["runs"][0]["candidate_count"], 1)
        self.assertEqual(history.json()["runs"][0]["screened_count"], 1)
        self.assertEqual(screening.status_code, 200)
        self.assertEqual(screening.json()["results"][0]["title"], "Mindfulness and fear of falling")
        self.assertEqual(screening.json()["results"][0]["screening_status"], "机器筛选完成")

    def test_search_results_endpoint_reads_the_current_run_artifact(self):
        root = Path(self.temp_root.name)
        run = create_research_run("Review a psychology intervention")
        api_server.research_run_store(root).create(run)
        artifacts = FileSystemArtifactStore(root / "runs" / run.run_id / "artifacts")
        artifacts.save_json(
            GOAL_ARTIFACT,
            AgentGoal(
                objective=run.research_question,
                research_question=run.research_question,
            ).model_dump(mode="json"),
        )
        artifacts.save_json(
            "search_results.json",
            {
                "config": {"queries": [{"id": "q1", "query": "question"}]},
                "report": {"query_count": 1, "deduplicated_candidate_count": 1},
                "papers": [{"paper_id": "paper-1", "title": "A paper"}],
            },
        )

        response = self.client.get(f"/v1/agent/runs/{run.run_id}/search-results")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["papers"][0]["title"], "A paper")
        self.assertEqual(response.json()["queries"][0]["id"], "q1")

    def test_human_gate_snapshot_includes_paper_context_for_review(self):
        root = Path(self.temp_root.name)
        run = create_research_run("Review a psychology intervention")
        RunStateMachine().transition_status(run, RunStatus.RUNNING)
        api_server.research_run_store(root).create(run)
        api_server.human_gate(root).request_action(
            run,
            action_type=HumanActionType.SCREENING_REVIEW_REQUIRED,
            reason="Please review the screening decision.",
            related_paper_ids=["paper-1"],
        )
        self._save_screening_artifacts(run.run_id)

        response = self.client.get(f"/v1/agent/runs/{run.run_id}")

        self.assertEqual(response.status_code, 200)
        context = response.json()["run"]["human_actions"][0]["paper_context"][0]
        self.assertEqual(context["title"], "Mindfulness and fear of falling")
        self.assertEqual(context["relevance_score"], 9)

    @patch("psychology_evidence_agent.api_server.literature_search_service")
    def test_search_returns_normalized_metadata(self, mock_service_factory):
        mock_service_factory.return_value.search.return_value = [
            Paper(
                paper_id="https://openalex.org/W1",
                doi="10.1/example",
                title="A paper",
                abstract="Abstract",
                matched_queries=["web_search"],
                query_coverage=1,
                cited_by_count=2,
            )
        ]
        response = self.client.post(
            "/v1/literature/search",
            json={
                "query": "fear of falling gait intervention",
                "year_from": 2018,
                "max_results": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["paper_count"], 1)
        mock_service_factory.return_value.search.assert_called_once()

    @patch("psychology_evidence_agent.api_server.literature_search_service")
    def test_configured_key_is_required(self, mock_service_factory):
        os.environ["PEA_API_KEY"] = "test-key"
        mock_service_factory.return_value.search.return_value = []
        denied = self.client.post("/v1/literature/search", json={"query": "fear of falling"})
        allowed = self.client.post(
            "/v1/literature/search",
            headers={"X-API-Key": "test-key"},
            json={"query": "fear of falling"},
        )
        self.assertEqual(denied.status_code, 401)
        self.assertNotEqual(allowed.status_code, 401)

    @patch("psychology_evidence_agent.api_server.full_text_service")
    def test_open_access_lookup_never_downloads(self, mock_lookup):
        from psychology_evidence_agent.domain.fulltext import FullTextCandidate

        mock_lookup.return_value.discover_for_paper.return_value = FullTextCandidate(
            retrieval_source="openalex",
            paper_id="https://openalex.org/W1",
            is_open_access=True,
            access_status="open_pdf_available",
            next_step="Download from the confirmed public link.",
        )
        response = self.client.post(
            "/v1/open-access/lookup",
            json={"papers": [{"paper_id": "https://openalex.org/W1", "title": "A paper"}]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["candidates"][0]["access_status"], "open_pdf_available")

    def test_agent_run_snapshot_reads_persisted_state_and_artifacts(self):
        root = Path(self.temp_root.name)
        run = create_research_run("A test research goal")
        api_server.research_run_store(root).create(run)
        artifacts = FileSystemArtifactStore(root / "runs" / run.run_id / "artifacts")
        artifacts.save_json(
            GOAL_ARTIFACT,
            AgentGoal(
                objective=run.research_question, research_question=run.research_question
            ).model_dump(mode="json"),
        )
        artifacts.save_json(BUDGET_ARTIFACT, ExecutionBudget().model_dump(mode="json"))

        response = self.client.get(f"/v1/agent/runs/{run.run_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["run_id"], run.run_id)
        self.assertEqual(response.json()["goal"]["objective"], run.research_question)
        self.assertFalse(response.json()["job_running"])
        self.assertTrue(response.json()["recovery_available"] is False)
        self.assertEqual(response.json()["progress"]["stage_count"], 7)
        self.assertEqual(response.json()["progress"]["current_stage"], "initializing")

    @patch("psychology_evidence_agent.api_server._submit_agent_job")
    def test_agent_run_creation_persists_goal_and_returns_accepted(self, mock_submit):
        response = self.client.post(
            "/v1/agent/runs",
            json={"objective": "Test a browser-created research run"},
        )

        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run"]["run_id"]
        self.assertEqual(response.json()["run"]["status"], "created")
        self.assertTrue(
            FileSystemArtifactStore(
                Path(self.temp_root.name) / "runs" / run_id / "artifacts"
            ).load_json(GOAL_ARTIFACT)["objective"]
        )
        mock_submit.assert_called_once()

    @patch("psychology_evidence_agent.api_server._submit_agent_job")
    def test_agent_run_creation_persists_structured_research_brief(self, mock_submit):
        response = self.client.post(
            "/v1/agent/runs",
            json={
                "objective": "Study an intervention",
                "research_question": "What changes in older adults?",
                "population": "Older adults",
                "outcomes": ["fear of falling", "HRV"],
                "inclusion_criteria": ["Peer-reviewed"],
                "year_from": 2015,
                "year_to": 2026,
            },
        )

        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run"]["run_id"]
        artifacts = FileSystemArtifactStore(
            Path(self.temp_root.name) / "runs" / run_id / "artifacts"
        )
        goal = artifacts.load_json(GOAL_ARTIFACT)
        brief = artifacts.load_json(BRIEF_ARTIFACT)
        self.assertEqual(goal["brief"]["population"], "Older adults")
        self.assertEqual(brief["research_question"], "What changes in older adults?")
        self.assertEqual(brief["outcomes"], ["fear of falling", "HRV"])
        mock_submit.assert_called_once()

    @patch("psychology_evidence_agent.api_server._submit_agent_job")
    def test_agent_run_creation_persists_manual_search_controls(self, mock_submit):
        response = self.client.post(
            "/v1/agent/runs",
            json={
                "objective": "Study a manually bounded search",
                "search_mode": "manual",
                "manual_query": "older adults AND intervention",
                "year_from": 2015,
                "year_to": 2022,
                "candidate_limit": 12,
            },
        )

        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run"]["run_id"]
        artifacts = FileSystemArtifactStore(
            Path(self.temp_root.name) / "runs" / run_id / "artifacts"
        )
        goal = artifacts.load_json(GOAL_ARTIFACT)
        options = artifacts.load_json("workflow_options.json")
        self.assertEqual(goal["search_preferences"]["mode"], "manual")
        self.assertEqual(
            goal["search_preferences"]["manual_query"], "older adults AND intervention"
        )
        self.assertEqual(options["candidate_limit"], 12)
        self.assertEqual(options["max_screen"], 12)
        mock_submit.assert_called_once()

    @patch("psychology_evidence_agent.api_server._submit_agent_job")
    def test_agent_run_keeps_external_key_out_of_persisted_artifacts(self, mock_submit):
        response = self.client.post(
            "/v1/agent/runs",
            json={
                "objective": "Study a model-routed research run",
                "model_connection": {
                    "provider": "openai",
                    "model": "account-model-id",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "never-persist-this-key",
                },
            },
        )

        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run"]["run_id"]
        artifacts = FileSystemArtifactStore(
            Path(self.temp_root.name) / "runs" / run_id / "artifacts"
        )
        options = artifacts.load_json(api_server.AGENT_OPTIONS_ARTIFACT)
        self.assertEqual(
            options["model_connection"],
            {"provider": "openai", "model": "account-model-id"},
        )
        artifact_text = "".join(
            path.read_text(encoding="utf-8")
            for path in (Path(self.temp_root.name) / "runs" / run_id).rglob("*.json")
        )
        self.assertNotIn("never-persist-this-key", artifact_text)
        self.assertNotIn("never-persist-this-key", response.text)
        mock_submit.assert_called_once()

    def test_agent_run_rejects_invalid_research_year_range(self):
        response = self.client.post(
            "/v1/agent/runs",
            json={"objective": "Study an intervention", "year_from": 2026, "year_to": 2015},
        )

        self.assertEqual(response.status_code, 422)

    @patch("psychology_evidence_agent.api_server._job_running", return_value=True)
    def test_cancel_requests_cooperative_stop_without_waiting_for_worker_lock(self, _job_running):
        root = Path(self.temp_root.name)
        run = create_research_run("取消中的研究")
        api_server.research_run_store(root).create(run)
        response = self.client.post(f"/v1/agent/runs/{run.run_id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["status"], "created")
        self.assertTrue(response.json()["cancel_requested"])
        self.assertTrue(api_server._cancellation_store(root).is_requested(run.run_id))

    def test_cancelled_orphaned_run_is_persisted_and_audited(self):
        root = Path(self.temp_root.name)
        run = create_research_run("取消孤立运行")
        RunStateMachine().transition_status(run, RunStatus.RUNNING)
        api_server.research_run_store(root).create(run)

        response = self.client.post(f"/v1/agent/runs/{run.run_id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["status"], "cancelled")
        events = api_server.FileSystemAgentEventStore(
            root / "runs" / run.run_id / "events.jsonl"
        ).read()
        self.assertEqual(events[-1].event_type, AgentEventType.RUN_CANCELLED)

    def _create_pending_fulltext_run(self):
        root = Path(self.temp_root.name)
        run = create_research_run("A full-text upload test")
        state_machine = RunStateMachine()
        state_machine.transition_status(run, RunStatus.RUNNING)
        state_machine.transition_stage(run, RunStage.SEARCHING)
        state_machine.transition_stage(run, RunStage.SCREENING)
        state_machine.transition_stage(run, RunStage.RETRIEVING_FULLTEXT)
        api_server.research_run_store(root).create(run)
        action = api_server.human_gate(root).request_action(
            run,
            action_type=HumanActionType.FULLTEXT_REQUIRED,
            reason="No legal full text was available.",
            related_paper_ids=["paper-1"],
        )
        artifacts = FileSystemArtifactStore(root / "runs" / run.run_id / "artifacts")
        artifacts.save_json(
            GOAL_ARTIFACT,
            AgentGoal(
                objective=run.research_question, research_question=run.research_question
            ).model_dump(mode="json"),
        )
        artifacts.save_json(api_server.AGENT_OPTIONS_ARTIFACT, {"deterministic_planner": True})
        return run, action, artifacts

    @patch("psychology_evidence_agent.api_server._submit_agent_job")
    def test_browser_can_upload_lawful_fulltext_and_resolve_gate(self, mock_submit):
        run, action, artifacts = self._create_pending_fulltext_run()
        response = self.client.post(
            f"/v1/agent/runs/{run.run_id}/human-actions/{action.action_id}/fulltext",
            data={
                "lawful_access_confirmed": "true",
                "note": "Obtained through the university library.",
            },
            files={"file": ("lawful-paper.pdf", b"%PDF-1.7\nprovided", "application/pdf")},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["run"]["status"], "running")
        resolved = response.json()["run"]["human_actions"][0]
        self.assertEqual(resolved["decision"]["decision_type"], "provide_fulltext")
        reference = response.json()["run"]["artifact_references"][-1]
        self.assertEqual(reference["artifact_type"], "fulltext_document")
        self.assertEqual(
            (
                Path(self.temp_root.name)
                / "runs"
                / run.run_id
                / "artifacts"
                / reference["logical_key"]
            ).read_bytes(),
            b"%PDF-1.7\nprovided",
        )
        self.assertEqual(reference["metadata"]["source"], "human_provided")
        self.assertEqual(reference["metadata"]["lawful_access_confirmed"], "true")
        mock_submit.assert_called_once()

    @patch("psychology_evidence_agent.api_server._submit_agent_job")
    def test_browser_fulltext_upload_requires_lawful_access_confirmation(self, mock_submit):
        run, action, _artifacts = self._create_pending_fulltext_run()
        response = self.client.post(
            f"/v1/agent/runs/{run.run_id}/human-actions/{action.action_id}/fulltext",
            data={"lawful_access_confirmed": "false"},
            files={"file": ("lawful-paper.pdf", b"%PDF-1.7\nprovided", "application/pdf")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("lawful access", response.json()["detail"])
        self.assertFalse(
            (Path(self.temp_root.name) / "runs" / run.run_id / "artifacts" / "documents").exists()
        )
        mock_submit.assert_not_called()

    @patch("psychology_evidence_agent.api_server._submit_agent_job")
    def test_browser_fulltext_upload_rejects_invalid_pdf_header(self, mock_submit):
        run, action, _artifacts = self._create_pending_fulltext_run()
        response = self.client.post(
            f"/v1/agent/runs/{run.run_id}/human-actions/{action.action_id}/fulltext",
            data={"lawful_access_confirmed": "true"},
            files={"file": ("not-a-paper.pdf", b"not a pdf", "application/pdf")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("valid PDF header", response.json()["detail"])
        self.assertFalse(
            (Path(self.temp_root.name) / "runs" / run.run_id / "artifacts" / "documents").exists()
        )
        mock_submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
