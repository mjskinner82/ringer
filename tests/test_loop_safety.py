#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    LAUNCH_CLASS_THRESHOLD_MS,
    append_escalation,
    escalations_path,
    read_escalations,
    retry_decision,
)


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


if __name__ == "__main__":
    unittest.main()
