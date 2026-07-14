#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace as dataclass_replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    LAUNCH_CLASS_THRESHOLD_MS,
    Manifest,
    RingerRunner,
    VerifyResult,
    WorkerResult,
    append_escalation,
    clear_escalations,
    escalations_path,
    read_escalations,
    review_escalations,
    retry_decision,
    run_status_command,
)


def make_runner(root: Path) -> RingerRunner:
    state_dir = root / "state"
    artifact = ArtifactConfig(
        enabled=False,
        out_template=str(root / "artifact-{run_id}.html"),
        report_template=str(root / "report-{run_id}.html"),
        index_out=root / "index.html",
    )
    engine = EngineConfig(
        name="mock",
        bin=sys.executable,
        args_template=(),
        full_access_args=(),
        sandbox_args=(),
    )
    config = AppConfig(
        path=None,
        identity_default=None,
        state_dir=state_dir,
        dashboard_port_base=8787,
        hud_port=8700,
        hud_app_path=None,
        allow_full_access=False,
        eval=EvalConfig(backend="jsonl", jsonl_path=root / "runs.jsonl"),
        engines={"mock": engine},
        artifact=artifact,
    )
    manifest = Manifest.from_obj(
        {
            "run_name": "loop-safety-test",
            "workdir": str(root / "work"),
            "tasks": [
                {
                    "key": "task-one",
                    "engine": "mock",
                    "spec": "Run the deterministic test worker.",
                    "check": "echo FAIL: deterministic test failure; exit 1",
                }
            ],
        }
    )
    return RingerRunner(manifest, config, "test-agent", dashboard_enabled=False)


class RetryDecisionTests(unittest.TestCase):
    def test_fast_nonzero_fail_is_terminal_launch(self) -> None:
        self.assertEqual(retry_decision("FAIL", 406, 1, 2, 2), "terminal-launch")
        self.assertEqual(
            retry_decision("FAIL", LAUNCH_CLASS_THRESHOLD_MS - 1, 1, 2, 1),
            "terminal-launch",
        )

    def test_fast_clean_exit_fail_retries(self) -> None:
        # Exit 0 means the worker did work that flunked the check; the
        # failure-context retry is legitimate (mock engine relies on this).
        self.assertEqual(retry_decision("FAIL", 406, 1, 2, 0), "retry")
        self.assertEqual(retry_decision("FAIL", 406, 1, 2, None), "retry")

    def test_slow_fail_retries(self) -> None:
        self.assertEqual(retry_decision("FAIL", LAUNCH_CLASS_THRESHOLD_MS, 1, 2, 2), "retry")
        self.assertEqual(retry_decision("FAIL", 120_000, 1, 2, 1), "retry")

    def test_timeout_retries_regardless_of_duration(self) -> None:
        self.assertEqual(retry_decision("TIMEOUT", 5_000, 1, 2, None), "retry")

    def test_last_attempt_is_terminal(self) -> None:
        self.assertEqual(retry_decision("FAIL", 120_000, 2, 2, 1), "terminal")
        self.assertEqual(retry_decision("FAIL", 406, 2, 2, 2), "terminal")
        self.assertEqual(retry_decision("TIMEOUT", 900_000, 2, 2, None), "terminal")

    def test_error_and_pass_never_retry(self) -> None:
        self.assertEqual(retry_decision("ERROR", 50, 1, 2, 1), "terminal")
        self.assertEqual(retry_decision("PASS", 50_000, 1, 2, 0), "terminal")


class EscalationLedgerTests(unittest.TestCase):
    def test_append_and_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            append_escalation(state, {"task_key": "a", "verdict": "FAIL"})
            append_escalation(state, {"task_key": "b", "verdict": "TIMEOUT"})
            records = read_escalations(state)
            self.assertEqual([item["task_key"] for item in records], ["a", "b"])
            self.assertTrue(escalations_path(state).exists())

    def test_read_missing_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_escalations(Path(tmp)), [])

    def test_read_skips_corrupt_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            escalations_path(state).write_text(
                '{"task_key": "ok", "verdict": "FAIL"}\nnot-json\n',
                encoding="utf-8",
            )
            records = read_escalations(state)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["task_key"], "ok")

    def test_read_respects_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            for index in range(60):
                append_escalation(state, {"task_key": f"t{index}"})
            records = read_escalations(state, limit=50)
            self.assertEqual(len(records), 50)
            self.assertEqual(records[-1]["task_key"], "t59")

    def test_read_returns_every_record_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            for index in range(60):
                append_escalation(state, {"task_key": f"t{index}"})

            records = read_escalations(state)

            self.assertEqual(60, len(records))
            self.assertEqual("t0", records[0]["task_key"])

    def test_append_failure_is_reported_to_caller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")

            self.assertFalse(append_escalation(blocker / "state", {"task_key": "a"}))

    def test_recovery_deduplicates_legacy_task_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            append_escalation(state, {"run_id": "run", "task_key": "task"})
            append_escalation(
                state,
                {
                    "escalation_id": "run:task",
                    "run_id": "run",
                    "task_key": "task",
                },
            )

            self.assertEqual(1, len(read_escalations(state)))

    def test_clear_failure_is_loud_and_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            escalations_path(state).mkdir(parents=True)
            stdout = io.StringIO()
            stderr = io.StringIO()
            config = SimpleNamespace(state_dir=state)
            with mock.patch.dict(os.environ, {"RINGER_HOME": str(root / "ringer-home")}):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    result = run_status_command(
                        config,
                        SimpleNamespace(clear_escalations=True),
                    )

            self.assertEqual(1, result)
            self.assertIn("FAILED TO CLEAR", stderr.getvalue())

    def test_clear_displays_every_acknowledged_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            for index in range(60):
                append_escalation(
                    state,
                    {
                        "logged_at": "2026-07-13T00:00:00+00:00",
                        "run_id": "run",
                        "task_key": f"t{index}",
                        "verdict": "FAIL",
                        "log_path": "worker.log",
                    },
                )
            stdout = io.StringIO()
            config = SimpleNamespace(state_dir=state)
            with mock.patch.dict(os.environ, {"RINGER_HOME": str(root / "ringer-home")}):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        2,
                        run_status_command(config, SimpleNamespace(clear_escalations=False)),
                    )
                with redirect_stdout(stdout):
                    result = run_status_command(
                        config,
                        SimpleNamespace(clear_escalations=True),
                    )

            output = stdout.getvalue()
            self.assertEqual(0, result)
            self.assertIn("acknowledged and cleared escalations: 60", output)
            self.assertIn(" t0: FAIL ", output)
            self.assertIn(" t59: FAIL ", output)
            self.assertEqual([], read_escalations(state))

    def test_append_waiting_on_clear_survives_in_new_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            append_escalation(state, {"task_key": "old"})
            _records, review_error = review_escalations(state)
            self.assertIsNone(review_error)
            original_read = ringer._read_escalations_unlocked
            writer: list[threading.Thread] = []
            blocked: list[bool] = []

            def slow_read(path: Path) -> list[dict[str, object]]:
                records = original_read(path)
                thread = threading.Thread(
                    target=lambda: append_escalation(state, {"task_key": "new"})
                )
                writer.append(thread)
                thread.start()
                time.sleep(0.05)
                blocked.append(thread.is_alive())
                return records

            with mock.patch("ringer._read_escalations_unlocked", side_effect=slow_read):
                cleared, error = clear_escalations(state)
            writer[0].join(timeout=2)

            self.assertIsNone(error)
            self.assertEqual(["old"], [record["task_key"] for record in cleared])
            self.assertEqual([True], blocked)
            self.assertEqual(["new"], [record["task_key"] for record in read_escalations(state)])

    def test_clear_acknowledges_only_the_last_status_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            config = SimpleNamespace(state_dir=state)
            append_escalation(
                state,
                {
                    "escalation_id": "run:old",
                    "run_id": "run",
                    "task_key": "old",
                    "verdict": "FAIL",
                },
            )
            with mock.patch.dict(os.environ, {"RINGER_HOME": str(root / "ringer-home")}):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        2,
                        run_status_command(config, SimpleNamespace(clear_escalations=False)),
                    )
                append_escalation(
                    state,
                    {
                        "escalation_id": "run:new",
                        "run_id": "run",
                        "task_key": "new",
                        "verdict": "FAIL",
                    },
                )
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    result = run_status_command(
                        config,
                        SimpleNamespace(clear_escalations=True),
                    )

            self.assertEqual(2, result)
            self.assertIn("acknowledged and cleared escalations: 1", stdout.getvalue())
            self.assertIn("new stranded escalations: 1", stdout.getvalue())
            self.assertEqual(
                ["new"],
                [record["task_key"] for record in read_escalations(state)],
            )

    def test_clear_refuses_unreviewed_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            append_escalation(
                state,
                {
                    "escalation_id": "run:task",
                    "run_id": "run",
                    "task_key": "task",
                    "verdict": "FAIL",
                },
            )

            cleared, error = clear_escalations(state)

            self.assertEqual([], cleared)
            self.assertIsNotNone(error)
            self.assertEqual(["task"], [record["task_key"] for record in read_escalations(state)])


class RunnerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_launch_class_uses_worker_elapsed_time_only(self) -> None:
        class SlowVerifier:
            async def verify(self, _task: object, _taskdir: Path) -> VerifyResult:
                await asyncio.sleep(0.05)
                return VerifyResult(
                    ok=False,
                    check_returncode=1,
                    check_timed_out=False,
                    raw_output_excerpt="FAIL: intentionally slow verifier",
                )

        with tempfile.TemporaryDirectory() as tmp:
            runner = make_runner(Path(tmp))
            runtime = runner.runtimes[0]
            runtime.taskdir.mkdir(parents=True)
            worker = mock.AsyncMock(
                return_value=WorkerResult(returncode=2, timed_out=False, tokens=None)
            )
            runner.verifier = SlowVerifier()
            with (
                mock.patch.object(runner, "_prepare_taskdir", new=mock.AsyncMock(return_value=(True, None))),
                mock.patch.object(runner, "_run_worker", new=worker),
                mock.patch.object(runner, "_log_attempt"),
                mock.patch.object(runner, "_record_task_escalation"),
                mock.patch.dict(os.environ, {"RINGER_LAUNCH_CLASS_THRESHOLD_MS": "10"}),
            ):
                await runner._run_task(runtime)
            runner.logger.close()

            self.assertEqual(1, worker.await_count)
            self.assertEqual("launch", runtime.failure_class)

    async def test_cancelled_run_records_each_unfinished_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = make_runner(root)
            started = asyncio.Event()

            async def wait_forever(_runtime: object) -> None:
                started.set()
                await asyncio.Future()

            runner._run_task = wait_forever  # type: ignore[method-assign]
            runner.kill_all_workers = mock.AsyncMock()  # type: ignore[method-assign]
            runner.state_writer.start = mock.Mock()
            runner.state_writer.flush = mock.Mock(return_value={})
            runner.state_writer.finish = mock.Mock()
            runner.state_writer.stop = mock.Mock()
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                task = asyncio.create_task(runner.run())
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            records = read_escalations(runner.config.state_dir)
            self.assertEqual(["task-one"], [record["task_key"] for record in records])
            self.assertEqual("cancelled", records[0]["failure_class"])

    async def test_terminal_escalation_warns_when_persistence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = make_runner(Path(tmp))
            runtime = runner.runtimes[0]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch("ringer.append_escalation", return_value=False):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    runner._record_task_escalation(runtime, "FAIL")
            runner.logger.close()

            self.assertNotIn("Recorded for", stdout.getvalue())
            self.assertIn("FAILED TO RECORD", stderr.getvalue())

    async def test_unexpected_runner_error_records_unfinished_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = make_runner(Path(tmp))

            async def fail_task(_runtime: object) -> None:
                raise RuntimeError("deterministic runner failure")

            runner._run_task = fail_task  # type: ignore[method-assign]
            runner.kill_all_workers = mock.AsyncMock()  # type: ignore[method-assign]
            runner.state_writer.start = mock.Mock()
            runner.state_writer.flush = mock.Mock(return_value={})
            runner.state_writer.finish = mock.Mock()
            runner.state_writer.stop = mock.Mock()
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                with self.assertRaisesRegex(RuntimeError, "deterministic runner failure"):
                    await runner.run()

            records = read_escalations(runner.config.state_dir)
            self.assertEqual(["task-one"], [record["task_key"] for record in records])
            self.assertEqual("orchestrator", records[0]["failure_class"])

    async def test_unexpected_runner_error_cancels_siblings_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = make_runner(Path(tmp))
            sibling = runner._task_runtime(
                dataclass_replace(runner.runtimes[0].task, key="task-two")
            )
            runner.runtimes.append(sibling)
            sibling_started = asyncio.Event()
            events: list[str] = []

            async def run_task(runtime: object) -> None:
                if runtime is sibling:
                    sibling_started.set()
                    try:
                        await asyncio.Future()
                    except asyncio.CancelledError:
                        events.append("sibling-cancelled")
                        raise
                await sibling_started.wait()
                raise RuntimeError("deterministic runner failure")

            async def kill_workers() -> None:
                events.append("workers-killed")

            runner._run_task = run_task  # type: ignore[method-assign]
            runner.kill_all_workers = kill_workers  # type: ignore[method-assign]
            runner.state_writer.start = mock.Mock()
            runner.state_writer.flush = mock.Mock(return_value={})
            runner.state_writer.finish = mock.Mock()
            runner.state_writer.stop = mock.Mock()
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "deterministic runner failure"):
                    await runner.run()

            self.assertEqual(["sibling-cancelled", "workers-killed"], events)

    async def test_kill_all_workers_waits_for_process_exit(self) -> None:
        class Process:
            pid = 123
            returncode: int | None = None
            wait_count = 0

            async def wait(self) -> int:
                self.wait_count += 1
                self.returncode = -9
                return self.returncode

        with tempfile.TemporaryDirectory() as tmp:
            runner = make_runner(Path(tmp))
            proc = Process()
            runner.active_processes[proc.pid] = proc  # type: ignore[assignment]
            with (
                mock.patch("ringer.terminate_process_group"),
                mock.patch("ringer.kill_process_group"),
                mock.patch("ringer.asyncio.sleep", new=mock.AsyncMock()),
            ):
                await runner.kill_all_workers()
            runner.logger.close()

            self.assertEqual(1, proc.wait_count)
            self.assertEqual({}, runner.active_processes)

    async def test_worker_error_defaults_to_launch_class(self) -> None:
        class FailingVerifier:
            async def verify(self, _task: object, _taskdir: Path) -> VerifyResult:
                return VerifyResult(
                    ok=False,
                    check_returncode=1,
                    check_timed_out=False,
                    raw_output_excerpt="worker did not launch",
                )

        with tempfile.TemporaryDirectory() as tmp:
            runner = make_runner(Path(tmp))
            runtime = runner.runtimes[0]
            runner.verifier = FailingVerifier()
            with (
                mock.patch.object(
                    runner,
                    "_run_worker",
                    new=mock.AsyncMock(
                        return_value=WorkerResult(
                            returncode=None,
                            timed_out=False,
                            tokens=None,
                            error="spawn failed",
                        )
                    ),
                ),
                mock.patch.object(runner, "_record_task_escalation"),
            ):
                await runner._run_task(runtime)
            runner.logger.close()

            self.assertEqual("launch", runtime.failure_class)

    async def test_full_access_gate_error_is_prepare_class(self) -> None:
        class FailingVerifier:
            async def verify(self, _task: object, _taskdir: Path) -> VerifyResult:
                return VerifyResult(
                    ok=False,
                    check_returncode=1,
                    check_timed_out=False,
                    raw_output_excerpt="worker did not launch",
                )

        with tempfile.TemporaryDirectory() as tmp:
            runner = make_runner(Path(tmp))
            runtime = runner.runtimes[0]
            runtime.task = dataclass_replace(runtime.task, full_access=True)
            runner.verifier = FailingVerifier()
            with mock.patch.object(runner, "_record_task_escalation"):
                await runner._run_task(runtime)
            runner.logger.close()

            self.assertEqual("prepare", runtime.failure_class)


if __name__ == "__main__":
    unittest.main()
