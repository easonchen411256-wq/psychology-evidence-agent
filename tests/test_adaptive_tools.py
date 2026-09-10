import tempfile
import unittest
from pathlib import Path

from psychology_evidence_agent.adapters.persistence.artifact_store import FileSystemArtifactStore
from psychology_evidence_agent.adapters.persistence.run_store import FileSystemResearchRunStore
from psychology_evidence_agent.domain.agent import (
    AgentGoal,
    AgentObservation,
    AgentPlan,
    ExecutionBudget,
    PlanStep,
    ResearchBrief,
    SearchPreferences,
    ToolArgumentSpec,
    ToolArgumentType,
    ToolDescriptor,
    ToolEffect,
)
from psychology_evidence_agent.domain.enums import (
    ArtifactType,
    HumanDecisionType,
    RunStage,
    RunStatus,
)
from psychology_evidence_agent.domain.errors import AgentPlanRejectedError, StructuredOutputError
from psychology_evidence_agent.domain.paper import Paper
from psychology_evidence_agent.domain.quality import QualityStatus
from psychology_evidence_agent.domain.run import HumanDecision, create_research_run
from psychology_evidence_agent.domain.screening import ScreeningDecision
from psychology_evidence_agent.runtime.human_gate import HumanGate
from psychology_evidence_agent.runtime.policy_guard import PlanPolicyGuard
from psychology_evidence_agent.runtime.state_machine import RunStateMachine
from psychology_evidence_agent.services.agent_tools import (
    AdaptiveScreeningExecuteTool,
    AdaptiveScreeningFinalizeTool,
    AdaptiveSearchCoverageTool,
    AdaptiveSearchFinalizeTool,
    AdaptiveSearchQueryTool,
)
from psychology_evidence_agent.services.planning import DeterministicPlanner


class FakeSearch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, int]] = []

    def search(self, *, query_id: str, query: str, year_from: int, per_page: int) -> list[Paper]:
        self.calls.append((query_id, query, year_from, per_page))
        return [
            Paper(
                paper_id="W1",
                doi="10.1234/example",
                title="Example paper",
                abstract="Abstract",
                matched_queries=[query_id],
                query_coverage=1,
            )
        ]


class YearBoundFakeSearch:
    def __init__(self) -> None:
        self.year_to: int | None = None

    def search(
        self,
        *,
        query_id: str,
        query: str,
        year_from: int,
        per_page: int,
        year_to: int | None = None,
    ) -> list[Paper]:
        del query_id, query, year_from, per_page
        self.year_to = year_to
        return [Paper(paper_id="bounded", title="Bounded paper")]


class FakeScreening:
    def __init__(self, *, human_review_note: str = "") -> None:
        self.human_review_note = human_review_note
        self.calls: list[list[str]] = []

    def screen_batch(self, papers: list[Paper], research_question: str):
        del research_question
        self.calls.append([paper.paper_id for paper in papers])
        return [
            ScreeningDecision(
                paper_id=paper.paper_id,
                relevance_score=8,
                evidence_level="direct",
                subtopic="psychology",
                rationale="Relevant metadata fixture.",
                human_review_note=self.human_review_note,
            )
            for paper in papers
        ]


class NoopHumanGate:
    def request_screening_review(self, *args, **kwargs):
        raise AssertionError("The fixture should not request a Human Gate.")


class AdaptiveToolTests(unittest.TestCase):
    def _save_search_results(self, store: FileSystemArtifactStore) -> None:
        store.save_json(
            "search_results.json",
            {
                "papers": [
                    Paper(paper_id="W1", title="Paper 1", query_coverage=2).model_dump(mode="json"),
                    Paper(paper_id="W2", title="Paper 2", query_coverage=1).model_dump(mode="json"),
                ]
            },
        )

    def test_typed_screening_batches_reuse_and_finalize_existing_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            self._save_search_results(store)
            fake_screening = FakeScreening()
            execute = AdaptiveScreeningExecuteTool(store, fake_screening)
            finalize = AdaptiveScreeningFinalizeTool(store, NoopHumanGate())
            run = create_research_run("Question")

            first = execute.execute(
                run,
                {"max_screen": 2, "screen_all": False, "batch_size": 1},
            )
            reused = execute.execute(
                run,
                {"max_screen": 2, "screen_all": False, "batch_size": 1},
            )
            final = finalize.execute(run, {})

            self.assertTrue(first.success)
            self.assertFalse(first.stage_complete)
            self.assertTrue(reused.success)
            self.assertEqual(fake_screening.calls, [["W1"], ["W2"]])
            self.assertTrue(final.success)
            self.assertTrue(final.stage_complete)
            payload = store.load_json("screening_results.json")
            self.assertEqual(
                [item["paper_id"] for item in payload["screened_papers"]], ["W1", "W2"]
            )
            self.assertEqual(
                [item["paper_id"] for item in payload["priority_papers"]], ["W1", "W2"]
            )

    def test_typed_screening_preserves_human_gate_and_can_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = FileSystemArtifactStore(root / "artifacts")
            self._save_search_results(store)
            run_store = FileSystemResearchRunStore(root)
            run = create_research_run("Question")
            run_store.create(run)
            RunStateMachine().transition_status(run, RunStatus.RUNNING)
            run_store.save(run)
            gate = HumanGate(RunStateMachine(), run_store)
            execute = AdaptiveScreeningExecuteTool(store, FakeScreening(human_review_note="Review"))
            finalize = AdaptiveScreeningFinalizeTool(store, gate)

            execute.execute(run, {"max_screen": 1, "screen_all": False, "batch_size": 1})
            blocked = finalize.execute(run, {})

            self.assertTrue(blocked.blocked)
            self.assertEqual(run.status, RunStatus.WAITING_FOR_HUMAN)
            self.assertTrue((root / "artifacts" / "screening_results.json").is_file())
            action = next(item for item in run.human_actions if item.decision is None)
            gate.resolve(
                run,
                HumanDecision(action_id=action.action_id, decision_type=HumanDecisionType.INCLUDE),
            )
            resumed = finalize.execute(run, {})

            self.assertTrue(resumed.success)
            self.assertEqual(run.status, RunStatus.RUNNING)

    def test_screening_retries_one_transient_structured_output_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            self._save_search_results(store)
            run = create_research_run("Question")

            class FlakyScreening(FakeScreening):
                def __init__(self) -> None:
                    super().__init__()
                    self.attempts = 0

                def screen_batch(self, papers, research_question):
                    self.attempts += 1
                    if self.attempts == 1:
                        raise StructuredOutputError("transient provider failure")
                    return super().screen_batch(papers, research_question)

            tool = AdaptiveScreeningExecuteTool(store, FlakyScreening())
            result = tool.execute(run, {"max_screen": 1, "batch_size": 1})

            self.assertTrue(result.success)

    def test_screening_plan_arguments_cannot_expand_user_workflow_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            self._save_search_results(store)
            store.save_json(
                "workflow_options.json",
                {"max_screen": 1, "screen_all": False, "screen_batch_size": 1},
            )
            fake_screening = FakeScreening()
            tool = AdaptiveScreeningExecuteTool(store, fake_screening)
            result = tool.execute(
                create_research_run("Question"),
                {"max_screen": 100, "screen_all": True, "batch_size": 100},
            )

            self.assertTrue(result.success)
            self.assertEqual(fake_screening.calls, [["W1"]])
            manifest = store.load_json("agent/screening_batches.json")
            self.assertEqual(
                manifest["settings"],
                {
                    "max_screen": 1,
                    "screen_all": False,
                    "screen_batch_size": 1,
                },
            )

    def test_query_and_finalize_tools_keep_search_stage_until_finalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            fake_search = FakeSearch()
            query_tool = AdaptiveSearchQueryTool(store, fake_search)
            finalize_tool = AdaptiveSearchFinalizeTool(store)
            coverage_tool = AdaptiveSearchCoverageTool(store, candidate_target=1)
            run = create_research_run("What changes in older adults?")

            first = query_tool.execute(
                run,
                {
                    "query_id": "q1",
                    "query": "older adults HRV",
                    "year_from": 2015,
                    "max_results": 10,
                },
            )
            second = query_tool.execute(
                run,
                {
                    "query_id": "q2",
                    "query": "older adults gait",
                    "year_from": 2015,
                    "max_results": 10,
                },
            )
            final = finalize_tool.execute(run, {})

            self.assertTrue(first.success)
            self.assertFalse(first.stage_complete)
            self.assertTrue(second.success)
            self.assertFalse(second.stage_complete)
            self.assertTrue(final.success)
            self.assertFalse(final.stage_complete)
            assessment = coverage_tool.execute(run, {})
            self.assertTrue(assessment.success)
            self.assertTrue(assessment.stage_complete)
            self.assertFalse(assessment.replan_required)
            self.assertEqual(len(fake_search.calls), 2)
            payload = store.load_json("search_results.json")
            self.assertEqual(len(payload["papers"]), 1)
            self.assertEqual(payload["papers"][0]["query_coverage"], 2)
            self.assertEqual(run.search_rounds[0].query_count, 2)
            quality = store.load_json("agent/research_quality_report.json")
            self.assertEqual(quality["status"], QualityStatus.SUFFICIENT)
            self.assertNotIn("abstract", quality)

    def test_query_tool_clamps_planner_request_to_persisted_workflow_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            fake_search = FakeSearch()
            tool = AdaptiveSearchQueryTool(store, fake_search)
            run = create_research_run("Question")

            result = tool.execute(
                run,
                {
                    "query_id": "q1",
                    "query": "question",
                    "max_results": 100,
                },
            )

            self.assertTrue(result.success)
            self.assertEqual(fake_search.calls[0][3], 15)

    def test_query_tool_passes_year_upper_bound_and_persists_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            fake_search = YearBoundFakeSearch()
            tool = AdaptiveSearchQueryTool(store, fake_search)
            run = create_research_run("Question")

            result = tool.execute(
                run,
                {
                    "query_id": "bounded",
                    "query": "question",
                    "year_from": 2015,
                    "year_to": 2022,
                },
            )

            self.assertTrue(result.success)
            self.assertEqual(fake_search.year_to, 2022)
            self.assertEqual(store.load_json("agent/search_queries/bounded.json")["year_to"], 2022)

    def test_finalize_applies_persisted_candidate_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            store.save_json(
                "workflow_options.json",
                {"max_per_query": 15, "candidate_limit": 1},
            )
            papers = [
                Paper(paper_id="W1", title="First"),
                Paper(paper_id="W2", title="Second"),
            ]
            store.save_json(
                "agent/search_queries.json",
                {
                    "queries": [
                        {
                            "query_id": "q1",
                            "query": "question",
                            "artifact_key": "agent/search_queries/q1.json",
                        }
                    ]
                },
            )
            store.save_json(
                "agent/search_queries/q1.json",
                {"papers": [paper.model_dump(mode="json") for paper in papers]},
            )

            result = AdaptiveSearchFinalizeTool(store).execute(create_research_run("Question"), {})

            self.assertTrue(result.success)
            self.assertEqual(len(store.load_json("search_results.json")["papers"]), 1)

    def test_deterministic_planner_honors_manual_query_and_year_range(self):
        goal = AgentGoal(
            objective="Question",
            research_question="Question",
            brief=ResearchBrief(research_question="Question", year_from=2015, year_to=2022),
            search_preferences=SearchPreferences(
                mode="manual", manual_query="exact search", candidate_limit=10
            ),
        )
        observation = AgentObservation(
            goal_id=goal.goal_id,
            current_stage=RunStage.SEARCHING,
            run_status=RunStatus.RUNNING,
            budget=ExecutionBudget(),
        )
        tools = [
            ToolDescriptor(
                name="search.execute_query",
                description="Search",
                stage=RunStage.SEARCHING,
                effect=ToolEffect.NETWORK,
            ),
            ToolDescriptor(
                name="search.finalize",
                description="Finalize",
                stage=RunStage.SEARCHING,
                effect=ToolEffect.LOCAL,
            ),
        ]

        plan = DeterministicPlanner().plan(goal, observation, tools)

        self.assertEqual(plan.steps[0].arguments["query"], "exact search")
        self.assertEqual(plan.steps[0].arguments["year_to"], 2022)

    def test_coverage_requests_replan_when_candidate_target_is_not_met(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            query_tool = AdaptiveSearchQueryTool(store, FakeSearch())
            finalize_tool = AdaptiveSearchFinalizeTool(store)
            coverage_tool = AdaptiveSearchCoverageTool(store, candidate_target=5)
            run = create_research_run("Question")

            query_tool.execute(run, {"query_id": "q1", "query": "question"})
            finalize_tool.execute(run, {})
            result = coverage_tool.execute(run, {})

            self.assertTrue(result.success)
            self.assertFalse(result.stage_complete)
            self.assertTrue(result.replan_required)
            report = store.load_json("agent/research_quality_report.json")
            self.assertEqual(report["status"], QualityStatus.NEEDS_MORE_SEARCH)
            self.assertEqual(report["recommended_action"], "replan_search")

    def test_coverage_recovers_when_planner_runs_it_before_explicit_finalize(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            query_tool = AdaptiveSearchQueryTool(store, FakeSearch())
            coverage_tool = AdaptiveSearchCoverageTool(store, candidate_target=1)
            run = create_research_run("Question")

            query_tool.execute(run, {"query_id": "q1", "query": "question"})
            result = coverage_tool.execute(run, {})

            self.assertTrue(result.success)
            self.assertTrue((Path(temporary) / "search_results.json").is_file())
            self.assertEqual(
                [reference.artifact_type for reference in result.artifact_references],
                [ArtifactType.SEARCH_RESULTS, ArtifactType.RESEARCH_QUALITY_REPORT],
            )

    def test_coverage_stops_safely_at_search_round_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            query_tool = AdaptiveSearchQueryTool(store, FakeSearch())
            finalize_tool = AdaptiveSearchFinalizeTool(store)
            coverage_tool = AdaptiveSearchCoverageTool(
                store, candidate_target=5, max_search_rounds=1
            )
            run = create_research_run("Question")

            query_tool.execute(run, {"query_id": "q1", "query": "question"})
            finalize_tool.execute(run, {})
            result = coverage_tool.execute(run, {})

            self.assertFalse(result.success)
            self.assertFalse(result.retryable)
            report = store.load_json("agent/research_quality_report.json")
            self.assertEqual(report["status"], QualityStatus.LIMIT_REACHED)
            self.assertEqual(report["recommended_action"], "stop")

    def test_query_tool_reuses_same_input_without_second_provider_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            fake_search = FakeSearch()
            tool = AdaptiveSearchQueryTool(store, fake_search)
            run = create_research_run("Question")
            arguments = {
                "query_id": "q1",
                "query": "same query",
                "year_from": 2020,
                "max_results": 5,
            }

            tool.execute(run, arguments)
            reused = tool.execute(run, arguments)

            self.assertTrue(reused.success)
            self.assertEqual(len(fake_search.calls), 1)

    def test_policy_guard_rejects_wrong_declared_argument_type(self):
        descriptor = ToolDescriptor(
            name="search.execute_query",
            description="Search",
            stage=RunStage.SEARCHING,
            effect=ToolEffect.NETWORK,
            argument_specs=[
                ToolArgumentSpec(
                    name="max_results",
                    value_type=ToolArgumentType.INTEGER,
                    required=True,
                )
            ],
        )

        class Registry:
            def descriptors(self):
                return [descriptor]

            def get(self, name):
                raise AssertionError(f"Unexpected tool lookup: {name}")

        goal = AgentGoal(objective="Question", research_question="Question")
        observation = AgentObservation(
            goal_id=goal.goal_id,
            current_stage=RunStage.SEARCHING,
            run_status=RunStatus.RUNNING,
            budget=ExecutionBudget(),
        )
        plan = AgentPlan(
            goal_id=goal.goal_id,
            steps=[
                PlanStep(
                    tool_name=descriptor.name,
                    arguments={"max_results": "10"},
                    success_criteria="Search",
                )
            ],
        )

        with self.assertRaises(AgentPlanRejectedError):
            PlanPolicyGuard().validate(plan, goal, observation, Registry())

    def test_deterministic_planner_emits_query_and_finalize_steps(self):
        goal = AgentGoal(objective="Question", research_question="Question")
        observation = AgentObservation(
            goal_id=goal.goal_id,
            current_stage=RunStage.SEARCHING,
            run_status=RunStatus.RUNNING,
            budget=ExecutionBudget(),
        )
        tools = [
            ToolDescriptor(
                name="search.execute_query",
                description="Search",
                stage=RunStage.SEARCHING,
                effect=ToolEffect.NETWORK,
            ),
            ToolDescriptor(
                name="search.finalize",
                description="Finalize",
                stage=RunStage.SEARCHING,
                effect=ToolEffect.LOCAL,
            ),
        ]

        plan = DeterministicPlanner().plan(goal, observation, tools)

        self.assertEqual(
            [step.tool_name for step in plan.steps], ["search.execute_query", "search.finalize"]
        )
        self.assertFalse(plan.steps[0].stage_complete)
        self.assertEqual(plan.steps[1].depends_on, [plan.steps[0].step_id])

    def test_deterministic_planner_adds_coverage_assessment_when_registered(self):
        goal = AgentGoal(objective="Question", research_question="Question")
        observation = AgentObservation(
            goal_id=goal.goal_id,
            current_stage=RunStage.SEARCHING,
            run_status=RunStatus.RUNNING,
            budget=ExecutionBudget(),
        )
        tools = [
            ToolDescriptor(
                name="search.execute_query",
                description="Search",
                stage=RunStage.SEARCHING,
                effect=ToolEffect.NETWORK,
            ),
            ToolDescriptor(
                name="search.finalize",
                description="Finalize",
                stage=RunStage.SEARCHING,
                effect=ToolEffect.LOCAL,
            ),
            ToolDescriptor(
                name="search.assess_coverage",
                description="Assess",
                stage=RunStage.SEARCHING,
                effect=ToolEffect.LOCAL,
            ),
        ]

        plan = DeterministicPlanner().plan(goal, observation, tools)

        self.assertEqual(
            [step.tool_name for step in plan.steps],
            ["search.execute_query", "search.finalize", "search.assess_coverage"],
        )
        self.assertEqual(plan.steps[2].depends_on, [plan.steps[1].step_id])

    def test_deterministic_planner_adds_typed_screening_steps_when_registered(self):
        goal = AgentGoal(objective="Question", research_question="Question")
        observation = AgentObservation(
            goal_id=goal.goal_id,
            current_stage=RunStage.SCREENING,
            run_status=RunStatus.RUNNING,
            budget=ExecutionBudget(),
        )
        tools = [
            ToolDescriptor(
                name="screen.execute_batches",
                description="Screen",
                stage=RunStage.SCREENING,
                effect=ToolEffect.MODEL,
            ),
            ToolDescriptor(
                name="screen.finalize",
                description="Finalize",
                stage=RunStage.SCREENING,
                effect=ToolEffect.LOCAL,
            ),
        ]

        plan = DeterministicPlanner().plan(goal, observation, tools)

        self.assertEqual(
            [step.tool_name for step in plan.steps], ["screen.execute_batches", "screen.finalize"]
        )
        self.assertEqual(plan.steps[1].depends_on, [plan.steps[0].step_id])


if __name__ == "__main__":
    unittest.main()
