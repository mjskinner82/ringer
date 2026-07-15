#!/usr/bin/env python3
from __future__ import annotations

import json
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    active_runs_path,
    read_active_runs,
    read_escalations,
    register_active_run,
    run_status_command,
    unregister_active_run,
)


class ActiveRunsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        os.environ["RINGER_HOME"] = str(Path(self.tmp.name) / "ringer-home")

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_register_read_unregister_round_trip_with_ringer_home_override(self) -> None:
        workdir = Path(self.tmp.name) / "work"
        register_active_run("run-1", "agent-a", "Test Run", workdir)

        runs = read_active_runs()
        self.assertEqual(["run-1"], sorted(runs))
        self.assertEqual(os.getpid(), runs["run-1"]["pid"])
        self.assertEqual("agent-a", runs["run-1"]["identity"])
        self.assertEqual("Test Run", runs["run-1"]["run_name"])
        self.assertEqual(str(workdir), runs["run-1"]["workdir"])
        self.assertTrue(runs["run-1"]["started_at"])
        self.assertEqual(
            (Path(os.environ["RINGER_HOME"]) / "active-runs.json").resolve(),
            active_runs_path(),
        )

        unregister_active_run("run-1")
        self.assertEqual({}, read_active_runs())

    def test_atomic_write_leaves_valid_json_and_no_temp_file(self) -> None:
        register_active_run("run-atomic", "agent-a", "Atomic", Path(self.tmp.name))

        marker = active_runs_path()
        self.assertTrue(marker.exists())
        data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertIn("run-atomic", data)
        self.assertEqual([], list(marker.parent.glob("*.tmp")))
        self.assertEqual([], list(marker.parent.glob(".*.tmp")))

    def test_dead_pid_is_retained_until_matching_recovery(self) -> None:
        marker = active_runs_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "dead-run": {
                        "pid": 99999999,
                        "identity": "old",
                        "run_name": "Dead",
                        "workdir": str(Path(self.tmp.name) / "dead"),
                        "started_at": "2026-07-05T00:00:00+00:00",
                    }
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual({}, read_active_runs(Path(self.tmp.name) / "other-state"))
        self.assertEqual(["dead-run"], sorted(json.loads(marker.read_text(encoding="utf-8"))))
        self.assertEqual([], read_escalations(Path(os.environ["RINGER_HOME"])))

        self.assertEqual({}, read_active_runs(Path(os.environ["RINGER_HOME"])))
        self.assertEqual({}, json.loads(marker.read_text(encoding="utf-8")))
        records = read_escalations(Path(os.environ["RINGER_HOME"]))
        self.assertEqual(["(unknown-task)"], [record["task_key"] for record in records])
        self.assertEqual("orchestrator", records[0]["failure_class"])

        register_active_run("live-run", "agent-a", "Live", Path(self.tmp.name))
        data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(["live-run"], sorted(data))

    def test_dead_run_recovers_each_nonpass_task_from_run_state(self) -> None:
        state_dir = Path(self.tmp.name) / "state"
        state_path = state_dir / "runs" / "dead-run.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps(
                {
                    "run_id": "dead-run",
                    "tasks": [
                        {
                            "key": "done",
                            "status": "pass",
                            "verdict": "PASS",
                            "attempts": 1,
                            "log_path": str(Path(self.tmp.name) / "done.log"),
                        },
                        {
                            "key": "stranded",
                            "status": "running",
                            "attempts": 1,
                            "log_path": str(Path(self.tmp.name) / "stranded.log"),
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        register_active_run(
            "dead-run",
            "agent-a",
            "Dead",
            Path(self.tmp.name),
            pid=99999999,
            state_dir=state_dir,
            state_path=state_path,
            tasks=[
                {"key": "done", "log_path": str(Path(self.tmp.name) / "done.log")},
                {"key": "stranded", "log_path": str(Path(self.tmp.name) / "stranded.log")},
            ],
        )

        self.assertEqual({}, read_active_runs(state_dir))
        records = read_escalations(state_dir)
        self.assertEqual(["stranded"], [record["task_key"] for record in records])
        self.assertEqual("ERROR", records[0]["verdict"])
        self.assertEqual("orchestrator", records[0]["failure_class"])

    def test_dead_run_is_retained_when_recovery_cannot_persist(self) -> None:
        state_dir = Path(self.tmp.name) / "state"
        register_active_run(
            "dead-run",
            "agent-a",
            "Dead",
            Path(self.tmp.name),
            pid=99999999,
            state_dir=state_dir,
            tasks=[{"key": "stranded", "log_path": str(Path(self.tmp.name) / "worker.log")}],
        )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("ringer.append_escalation", return_value=False):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = run_status_command(
                    SimpleNamespace(state_dir=state_dir),
                    SimpleNamespace(clear_escalations=False),
                )
            retained = json.loads(active_runs_path().read_text(encoding="utf-8"))

        self.assertEqual(1, result)
        self.assertIn("FAILED TO RECORD recovered escalations", stderr.getvalue())
        self.assertEqual(["dead-run"], sorted(retained))

    def test_dead_run_waits_for_matching_state_directory(self) -> None:
        state_dir = Path(self.tmp.name) / "state-a"
        other_state_dir = Path(self.tmp.name) / "state-b"
        register_active_run(
            "dead-run",
            "agent-a",
            "Dead",
            Path(self.tmp.name),
            pid=99999999,
            state_dir=state_dir,
            tasks=[{"key": "stranded", "log_path": str(Path(self.tmp.name) / "worker.log")}],
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = run_status_command(
                SimpleNamespace(state_dir=other_state_dir),
                SimpleNamespace(clear_escalations=False),
            )

        self.assertEqual(2, result)
        self.assertIn("dead runs awaiting escalation recovery: 1", stdout.getvalue())
        retained = json.loads(active_runs_path().read_text(encoding="utf-8"))
        self.assertEqual(["dead-run"], sorted(retained))
        self.assertEqual([], read_escalations(state_dir))

        self.assertEqual({}, read_active_runs(state_dir))
        self.assertEqual(["stranded"], [item["task_key"] for item in read_escalations(state_dir)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
