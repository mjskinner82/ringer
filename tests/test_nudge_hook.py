#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "ringer_nudge.py"
NUDGE_TEXT = (
    "Ringer advisory: this command appears to call a model provider or "
    "conversational harness outside a live Ringer run. Do not load or launch "
    "Ringer automatically. If the user selected Ringer or the closest repository "
    "instructions require it, use the Ringer skill. Otherwise continue directly "
    "and recommend Ringer at most once only when the job has at least two genuinely "
    "independent lanes."
)
NO_MISTAKES_FIX_OWNERSHIP_STATES = (
    "fixing",
    "awaiting_approval",
    "fix_review",
)


class NudgeHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.ringer_home = Path(self.temp.name) / "ringer"
        self.bin_dir = Path(self.temp.name) / "bin"
        self.home.mkdir()
        self.ringer_home.mkdir()

    def run_hook(
        self,
        mode: str,
        payload: object | str,
        path: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["RINGER_HOME"] = str(self.ringer_home)
        if path is not None:
            env["PATH"] = path
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(
            [sys.executable, str(HOOK), mode],
            input=stdin,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def pre_bash_payload(
        self,
        command: str,
        session_id: str = "session-1",
        cwd: str = "/tmp/session-work",
    ) -> dict[str, object]:
        return {
            "session_id": session_id,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": cwd,
        }

    def assert_nudged(self, proc: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(0, proc.returncode)
        data = json.loads(proc.stdout)
        self.assertEqual(
            {
                "hookEventName": "PreToolUse",
                "additionalContext": NUDGE_TEXT,
            },
            data["hookSpecificOutput"],
        )

    def assert_silent(self, proc: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(0, proc.returncode)
        self.assertEqual("", proc.stdout)
        self.assertEqual("", proc.stderr)

    def write_active_run(self, pid: int, workdir: str = "/tmp/live-ringer-work") -> None:
        path = self.ringer_home / "active-runs.json"
        payload = {
            "run-live": {
                "pid": pid,
                "identity": "test-agent",
                "run_name": "test-run",
                "workdir": workdir,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def make_git_repo(self, branch: str = "feature/owned") -> Path:
        repo = Path(self.temp.name) / "repo"
        subprocess.run(
            ["git", "init", "--initial-branch", branch, str(repo)],
            capture_output=True,
            text=True,
            check=True,
        )
        return repo

    def install_no_mistakes(self, script: str) -> None:
        self.bin_dir.mkdir(exist_ok=True)
        executable = self.bin_dir / "no-mistakes"
        executable.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
        executable.chmod(0o755)

    def no_mistakes_path(self) -> str:
        return f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}"

    def toon_status(
        self,
        branch: str,
        rows: list[tuple[str, str]],
        run_status: str = "running",
    ) -> str:
        lines = [
            "run:",
            "  id: test-run",
            f"  branch: {branch}",
            f"  status: {run_status}",
            "  head: 8f3a6c1",
            "  steps[9]{step,status,findings,duration_ms}:",
        ]
        lines.extend(f"    {step},{status},1,123" for step, status in rows)
        return "\n".join(lines) + "\n"

    def toon_status_script(self, output: str) -> str:
        return "cat <<'STATUS'\n" + output + "STATUS"

    def test_pre_bash_nudges_on_harness_script(self) -> None:
        proc = self.run_hook("pre-bash", self.pre_bash_payload("node probe-simulate.mjs"))
        self.assert_nudged(proc)

    def test_pre_bash_nudges_on_provider_endpoint(self) -> None:
        payload = self.pre_bash_payload(
            "curl https://api.anthropic.com/v1/messages", "session-2"
        )
        self.assert_nudged(self.run_hook("pre-bash", payload))

    def test_pre_bash_stays_silent_on_ordinary_command(self) -> None:
        self.assert_silent(
            self.run_hook("pre-bash", self.pre_bash_payload("ls -la"))
        )

    def test_pre_bash_stays_silent_on_ringer_command(self) -> None:
        commands = (
            "python3 ringer.py run manifest.json",
            "/Users/mattskinner/.local/bin/ringer run manifest.json",
        )
        for index, command in enumerate(commands):
            with self.subTest(command=command):
                self.assert_silent(
                    self.run_hook(
                        "pre-bash",
                        self.pre_bash_payload(command, session_id=f"ringer-{index}"),
                    )
                )

    def test_pre_bash_stays_silent_inside_active_run_workdir(self) -> None:
        self.write_active_run(os.getpid())
        proc = self.run_hook(
            "pre-bash",
            self.pre_bash_payload(
                "node probe-simulate.mjs", cwd="/tmp/live-ringer-work/task"
            ),
        )
        self.assert_silent(proc)

    def test_unrelated_active_run_does_not_silence_session(self) -> None:
        self.write_active_run(os.getpid())
        proc = self.run_hook(
            "pre-bash",
            self.pre_bash_payload(
                "node probe-simulate.mjs",
                session_id="unrelated-session",
                cwd="/tmp/other-work",
            ),
        )
        self.assert_nudged(proc)

    def test_pre_bash_dedupes_per_session(self) -> None:
        first = self.run_hook(
            "pre-bash", self.pre_bash_payload("node probe-simulate.mjs")
        )
        second = self.run_hook(
            "pre-bash",
            self.pre_bash_payload("curl https://api.openai.com/v1/chat/completions"),
        )
        self.assert_nudged(first)
        self.assert_silent(second)

    def test_active_no_mistakes_fix_ownership_suppresses_advisory(self) -> None:
        repo = self.make_git_repo("feature/owned")
        ownership_rows = (("review", "running"),) + tuple(
            ("fix", state) for state in NO_MISTAKES_FIX_OWNERSHIP_STATES
        )
        for step, state in ownership_rows:
            with self.subTest(step=step, state=state):
                self.install_no_mistakes(
                    self.toon_status_script(
                        self.toon_status("feature/owned", [(step, state)])
                    )
                )
                proc = self.run_hook(
                    "pre-bash",
                    self.pre_bash_payload(
                        "node probe-simulate.mjs",
                        session_id=f"owned-{step}-{state}",
                        cwd=str(repo),
                    ),
                    path=self.no_mistakes_path(),
                )
                self.assert_silent(proc)

    def test_no_mistakes_on_another_branch_does_not_suppress_advisory(self) -> None:
        repo = self.make_git_repo("feature/owned")
        self.install_no_mistakes(
            self.toon_status_script(
                self.toon_status("feature/other", [("review", "running")])
            )
        )
        proc = self.run_hook(
            "pre-bash",
            self.pre_bash_payload(
                "node probe-simulate.mjs", "other-branch", str(repo)
            ),
            path=self.no_mistakes_path(),
        )
        self.assert_nudged(proc)

    def test_post_publication_monitor_does_not_suppress_advisory(self) -> None:
        repo = self.make_git_repo("feature/owned")
        self.install_no_mistakes(
            self.toon_status_script(
                self.toon_status(
                    "feature/owned",
                    [("review", "completed"), ("ci", "running")],
                )
            )
        )
        proc = self.run_hook(
            "pre-bash",
            self.pre_bash_payload("node probe-simulate.mjs", "ci-running", str(repo)),
            path=self.no_mistakes_path(),
        )
        self.assert_nudged(proc)

    def test_no_mistakes_probe_failures_fail_open(self) -> None:
        repo = self.make_git_repo()
        cases = {
            "absent": (None, "/usr/bin:/bin"),
            "error": ("exit 1", None),
            "malformed": ("printf '%s\\n' 'not a status response'", None),
            "timeout": ("sleep 2", None),
        }
        for name, (script, path) in cases.items():
            with self.subTest(name=name):
                if script is not None:
                    self.install_no_mistakes(script)
                    path = self.no_mistakes_path()
                proc = self.run_hook(
                    "pre-bash",
                    self.pre_bash_payload(
                        "node probe-simulate.mjs", f"fail-open-{name}", str(repo)
                    ),
                    path=path,
                )
                self.assert_nudged(proc)

    def test_removed_post_edit_mode_is_silent(self) -> None:
        payload = {
            "session_id": "post-edit",
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/a.py"},
        }
        self.assert_silent(self.run_hook("post-edit", payload))

    def test_malformed_stdin_exits_zero_silently(self) -> None:
        self.assert_silent(self.run_hook("pre-bash", "{not json"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
