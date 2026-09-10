import unittest
from datetime import UTC, datetime

from psychology_evidence_agent.domain.enums import ArtifactType, RunStage, RunStatus
from psychology_evidence_agent.domain.errors import (
    MissingStageInputError,
    StepExecutionError,
    UnsupportedStageError,
)
from psychology_evidence_agent.domain.run import ArtifactReference, RunFailure, create_research_run
from psychology_evidence_agent.runtime.step_executor import (
    STAGE_OUTPUT_ARTIFACTS,
    STAGE_REQUIRED_ARTIFACTS,
    StepExecutor,
)
from psychology_evidence_agent.runtime.step_result import StepResult


def reference(artifact_type: ArtifactType) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"{artifact_type.value}:one",
        artifact_type=artifact_type,
        logical_key=f"{artifact_type.value}/one.json",
    )


class StepExecutorTests(unittest.TestCase):
    def test_each_stage_handler_is_called_once_and_stage_is_not_advanced(self):
        for stage in RunStage:
            calls: list[RunStage] = []
            run = create_research_run("问题")
            run.status = RunStatus.RUNNING
            run.stage = stage
            run.artifact_references = [reference(item) for item in STAGE_REQUIRED_ARTIFACTS[stage]]
            outputs = [reference(item) for item in STAGE_OUTPUT_ARTIFACTS[stage]]

            def fake_service(_run, current=stage, output=outputs):
                calls.append(current)
                return StepResult(stage=current, success=True, artifact_references=output)

            result = StepExecutor({stage: fake_service}).execute_one_step(run)
            self.assertEqual(calls, [stage])
            self.assertEqual(result.stage, stage)
            self.assertEqual(run.stage, stage)

    def test_missing_required_artifact_fails_before_service_call(self):
        run = create_research_run("问题")
        run.status = RunStatus.RUNNING
        run.stage = RunStage.SYNTHESIZING
        called = False

        def handler(_run):
            nonlocal called
            called = True
            return StepResult(stage=RunStage.SYNTHESIZING, success=True)

        with self.assertRaisesRegex(MissingStageInputError, "evidence_synthesis|evidence_card"):
            StepExecutor({RunStage.SYNTHESIZING: handler}).execute_one_step(run)
        self.assertFalse(called)

    def test_unsupported_stage_and_service_failure_are_explicit(self):
        run = create_research_run("问题")
        run.status = RunStatus.RUNNING
        with self.assertRaises(UnsupportedStageError):
            StepExecutor({}).execute_one_step(run)

        run.stage = RunStage.SEARCHING

        def failing_handler(_run):
            raise RuntimeError("service failed")

        with self.assertRaisesRegex(StepExecutionError, "searching"):
            StepExecutor({RunStage.SEARCHING: failing_handler}).execute_one_step(run)

    def test_handler_cannot_return_a_different_stage_or_unexpected_artifact(self):
        run = create_research_run("问题")
        run.status = RunStatus.RUNNING
        run.stage = RunStage.SEARCHING
        with self.assertRaises(StepExecutionError):
            StepExecutor(
                {RunStage.SEARCHING: lambda _: StepResult(stage=RunStage.SCREENING, success=True)}
            ).execute_one_step(run)

        with self.assertRaises(StepExecutionError):
            StepExecutor(
                {
                    RunStage.SEARCHING: lambda _: StepResult(
                        stage=RunStage.SEARCHING,
                        success=True,
                        artifact_references=[reference(ArtifactType.EVIDENCE_CARD)],
                    )
                }
            ).execute_one_step(run)

    def test_successful_stage_must_publish_its_required_output(self):
        run = create_research_run("问题")
        run.status = RunStatus.RUNNING
        run.stage = RunStage.SEARCHING
        with self.assertRaisesRegex(StepExecutionError, "required artifact"):
            StepExecutor(
                {RunStage.SEARCHING: lambda _: StepResult(stage=RunStage.SEARCHING, success=True)}
            ).execute_one_step(run)

    def test_step_result_requires_consistent_success_and_failure(self):
        failure = RunFailure(
            error_code="temporary",
            message="retry",
            stage=RunStage.SEARCHING,
            retryable=True,
            occurred_at=datetime.now(UTC),
        )
        with self.assertRaises(ValueError):
            StepResult(stage=RunStage.SEARCHING, success=True, failure=failure)
        with self.assertRaises(ValueError):
            StepResult(stage=RunStage.SEARCHING, success=False)


if __name__ == "__main__":
    unittest.main()
