import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from psychology_evidence_agent import run_management
from psychology_evidence_agent.adapters.persistence.run_lock import FileSystemResearchRunLock
from psychology_evidence_agent.adapters.persistence.run_store import FileSystemResearchRunStore
from psychology_evidence_agent.domain.enums import RunStage, RunStatus
from psychology_evidence_agent.domain.run import RunFailure, create_research_run


class RunManagementTests(unittest.TestCase):
    def test_create_then_show_persisted_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output: list[str] = []
            with patch("builtins.print", side_effect=output.append):
                self.assertEqual(
                    run_management.main(
                        ["--store-root", str(root), "create", "研究问题：如何监测？"]
                    ),
                    0,
                )
            run_id = next(
                line.removeprefix("Run ID: ") for line in output if line.startswith("Run ID:")
            )
            shown: list[str] = []
            with patch("builtins.print", side_effect=shown.append):
                self.assertEqual(
                    run_management.main(["--store-root", str(root), "show", run_id]), 0
                )
            self.assertIn(run_id, shown[0])
            self.assertIn("研究问题：如何监测？", shown[0])

    def test_mutating_commands_fail_fast_when_run_is_busy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output: list[str] = []
            with patch("builtins.print", side_effect=output.append):
                self.assertEqual(
                    run_management.main(["--store-root", str(root), "create", "锁测试"]), 0
                )
            run_id = next(
                line.removeprefix("Run ID: ") for line in output if line.startswith("Run ID:")
            )

            with FileSystemResearchRunLock(root, run_id):
                blocked: list[str] = []
                with patch("builtins.print", side_effect=blocked.append):
                    result = run_management.main(["--store-root", str(root), "start", run_id])
            self.assertEqual(result, 2)
            self.assertIn("already active", " ".join(blocked))

    def test_cancel_persists_terminal_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output: list[str] = []
            with patch("builtins.print", side_effect=output.append):
                self.assertEqual(
                    run_management.main(["--store-root", str(root), "create", "取消测试"]), 0
                )
            run_id = next(
                line.removeprefix("Run ID: ") for line in output if line.startswith("Run ID:")
            )
            with patch("builtins.print"):
                self.assertEqual(
                    run_management.main(["--store-root", str(root), "cancel", run_id]), 0
                )
            shown: list[str] = []
            with patch("builtins.print", side_effect=shown.append):
                self.assertEqual(
                    run_management.main(["--store-root", str(root), "show", run_id]), 0
                )
            self.assertIn('"status": "cancelled"', shown[0])

    def test_retry_starts_agent_for_retryable_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = FileSystemResearchRunStore(root)
            run = create_research_run("重试测试")
            store.create(run)
            failed = store.load(run.run_id)
            failed.status = RunStatus.FAILED
            failed.record_failure(
                RunFailure(
                    error_code="temporary_provider_failure",
                    message="provider unavailable",
                    stage=RunStage.SEARCHING,
                    retryable=True,
                )
            )
            store.save(failed)

            agent = MagicMock()
            with patch(
                "psychology_evidence_agent.run_management.evidence_agent",
                return_value=agent,
            ) as factory:
                result = run_management.main(["--store-root", str(root), "retry", run.run_id])

            self.assertEqual(result, 0)
            factory.assert_called_once()
            agent.run_until_blocked.assert_called_once()
            self.assertEqual(agent.run_until_blocked.call_args.args[0].status, RunStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
