import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from psychology_evidence_agent.adapters.persistence.artifact_store import FileSystemArtifactStore
from psychology_evidence_agent.adapters.persistence.cancellation_store import (
    FileSystemCancellationStore,
)
from psychology_evidence_agent.adapters.persistence.run_lock import FileSystemResearchRunLock
from psychology_evidence_agent.adapters.persistence.run_store import FileSystemResearchRunStore
from psychology_evidence_agent.domain.enums import ArtifactType, RunStage, RunStatus
from psychology_evidence_agent.domain.errors import (
    RunAlreadyExistsError,
    RunBusyError,
    RunConcurrencyError,
    RunNotFoundError,
    RunPersistenceError,
)
from psychology_evidence_agent.domain.run import (
    ArtifactReference,
    RunFailure,
    create_research_run,
)


class PersistenceTests(unittest.TestCase):
    def test_cancellation_signal_round_trip_isolated_per_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = create_research_run("取消一")
            second = create_research_run("取消二")
            store = FileSystemCancellationStore(root)

            self.assertFalse(store.is_requested(first.run_id))
            store.request(first.run_id)
            self.assertTrue(store.is_requested(first.run_id))
            self.assertFalse(store.is_requested(second.run_id))
            store.clear(first.run_id)
            self.assertFalse(store.is_requested(first.run_id))

    def test_research_run_lock_is_exclusive_and_releases_after_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = create_research_run("锁测试")
            first = FileSystemResearchRunLock(root, run.run_id)
            second = FileSystemResearchRunLock(root, run.run_id)

            with first:
                with self.assertRaises(RunBusyError):
                    second.__enter__()
            with second:
                self.assertTrue(second.path.is_file())

    def test_research_run_lock_blocks_a_second_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = create_research_run("跨进程锁测试")
            script = (
                "from pathlib import Path; import sys, time; "
                "from psychology_evidence_agent.adapters.persistence.run_lock import "
                "FileSystemResearchRunLock; "
                "lock = FileSystemResearchRunLock(Path(sys.argv[1]), sys.argv[2]); "
                "lock.__enter__(); print('locked', flush=True); time.sleep(10)"
            )
            source_root = str(Path(__file__).resolve().parents[1] / "src")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join(
                item for item in (source_root, environment.get("PYTHONPATH", "")) if item
            )
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(root), run.run_id],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                with self.assertRaises(RunBusyError):
                    FileSystemResearchRunLock(root, run.run_id).__enter__()
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_artifact_round_trip_and_nested_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = FileSystemArtifactStore(root / "artifacts")
            path = store.save_json("run-a/unicode.json", {"title": "证据", "items": [1]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["title"], "证据")
            self.assertEqual(store.load_json("run-a/unicode.json")["items"], [1])
            self.assertEqual(
                store.save_text("run-a/note.md", "中文"), root / "artifacts/run-a/note.md"
            )
            self.assertEqual(store.read_text("run-a/note.md"), "中文")
            binary_path = store.save_bytes("run-a/report.docx", b"PK\x03\x04")
            self.assertEqual(binary_path.read_bytes(), b"PK\x03\x04")
            reference = store.reference("run-a/unicode.json", ArtifactType.SEARCH_RESULTS)
            self.assertEqual(reference.logical_key, "run-a/unicode.json")
            self.assertEqual(reference.artifact_type, ArtifactType.SEARCH_RESULTS)

    def test_runs_are_isolated_and_cwd_does_not_matter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runs"
            store = FileSystemResearchRunStore(root)
            first = create_research_run("问题一")
            second = create_research_run("问题二")
            store.create(first)
            store.create(second)
            self.assertTrue(store.exists(first.run_id))
            self.assertTrue(store.exists(second.run_id))
            original = Path.cwd()
            try:
                os.chdir(Path(temporary).parent)
                self.assertEqual(store.load(first.run_id), first)
            finally:
                os.chdir(original)

    def test_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemArtifactStore(Path(temporary))
            with self.assertRaises(ValueError):
                store.save_text("../outside.txt", "blocked")

    def test_research_run_store_errors_and_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = create_research_run("Unicode 研究问题：跌倒恐惧")
            reference = ArtifactReference(
                artifact_id="search-1",
                artifact_type=ArtifactType.SEARCH_RESULTS,
                logical_key="search/candidates.json",
            )
            run.add_artifact(reference)
            run.record_failure(
                RunFailure(
                    error_code="provider_unavailable",
                    message="暂时不可用",
                    stage=RunStage.SEARCHING,
                    retryable=True,
                    provider="openalex",
                )
            )
            store1 = FileSystemResearchRunStore(root)
            store1.create(run)
            self.assertTrue((root / "runs" / run.run_id / "state.json").is_file())
            with self.assertRaises(RunAlreadyExistsError):
                store1.create(run)

            run.status = RunStatus.FAILED
            store1.save(run)
            del store1
            store2 = FileSystemResearchRunStore(root)
            self.assertEqual(store2.load(run.run_id), run)
            self.assertEqual(store2.load(run.run_id).artifact_references[0], reference)
            with self.assertRaises(RunNotFoundError):
                store2.load("run_00000000000000000000000000000000")

            malformed = root / "runs" / "run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            malformed.mkdir(parents=True)
            (malformed / "state.json").write_text("{bad", encoding="utf-8")
            with self.assertRaises(RunPersistenceError):
                store2.load("run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    def test_stale_run_snapshot_cannot_overwrite_newer_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSystemResearchRunStore(Path(temporary))
            original = create_research_run("乐观并发控制")
            store.create(original)
            first = store.load(original.run_id)
            stale = store.load(original.run_id)

            first.touch()
            store.save(first)
            stale.touch()
            with self.assertRaises(RunConcurrencyError):
                store.save(stale)

            restored = store.load(original.run_id)
            self.assertEqual(restored.revision, 1)
            self.assertEqual(stale.revision, 0)
