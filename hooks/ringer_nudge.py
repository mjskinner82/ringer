#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NUDGE_TEXT = (
    "Ringer advisory: this command appears to call a model provider or "
    "conversational harness outside a live Ringer run. Do not load or launch "
    "Ringer automatically. If the user selected Ringer or the closest repository "
    "instructions require it, use the Ringer skill. Otherwise continue directly "
    "and recommend Ringer at most once only when the job has at least two genuinely "
    "independent lanes."
)

NO_MISTAKES_FIX_OWNERSHIP_STATES = {
    "fixing",
    "awaiting_approval",
    "fix_review",
}
NO_MISTAKES_PROBE_TIMEOUT_SECONDS = 1.5
TOON_TABLE_HEADER_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9_-]*)(?:\[\d+\])?\{(?P<columns>[^}]+)\}:$"
)
POST_PUBLICATION_STEPS = {
    "ci",
    "deploy",
    "deployment",
    "publish",
    "published",
    "publication",
}

PROVIDER_RE = re.compile(
    r"(api\.anthropic\.com|api\.openai\.com|openrouter\.ai|"
    r"generativelanguage\.googleapis|/v1/chat/completions|/v1/messages)",
    re.IGNORECASE,
)
HARNESS_RE = re.compile(
    r"\b(?:node|python3?|bun|deno)\s+\S*"
    r"(?:simulat|probe|smoke|harness|persona|grader|eval)\S*"
    r"\.(?:mjs|js|ts|py)\b",
    re.IGNORECASE,
)


def ringer_home() -> Path:
    value = os.environ.get("RINGER_HOME")
    if value and value.strip():
        return Path(value).expanduser()
    return Path.home() / ".ringer"


def pid_is_alive(pid: Any) -> bool:
    try:
        parsed = int(pid)
    except (TypeError, ValueError):
        return False
    if parsed <= 0:
        return False
    try:
        os.kill(parsed, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, path)


def read_live_active_runs(home: Path) -> dict[str, dict[str, Any]]:
    path = home / "active-runs.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        return {}

    live: dict[str, dict[str, Any]] = {}
    changed = False
    for run_id, entry in raw.items():
        if not isinstance(entry, dict):
            changed = True
            continue
        if pid_is_alive(entry.get("pid")):
            live[str(run_id)] = entry
        else:
            changed = True

    if changed:
        write_json_atomic(path, live)
    return live


def marker_path(home: Path, session_id: Any) -> Path:
    key = f"{session_id}\0pre-bash".encode("utf-8", errors="replace")
    digest = hashlib.sha256(key).hexdigest()
    return home / "nudge-state" / f"{digest}.pre-bash.nudged"


def claim_dedupe_marker(home: Path, session_id: Any) -> bool:
    directory = home / "nudge-state"
    directory.mkdir(parents=True, exist_ok=True)
    marker = marker_path(home, session_id)
    try:
        with marker.open("x", encoding="utf-8") as handle:
            handle.write(datetime.now(timezone.utc).isoformat())
            handle.write("\n")
    except FileExistsError:
        return False
    return True


def output_nudge() -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": NUDGE_TEXT,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


def command_references_active_workdir(
    command: str, active_runs: dict[str, dict[str, Any]]
) -> bool:
    for entry in active_runs.values():
        workdir = str(entry.get("workdir") or "").strip()
        if workdir and workdir in command:
            return True
    return False


def path_is_inside(path: str, root: str) -> bool:
    try:
        Path(path).expanduser().resolve().relative_to(Path(root).expanduser().resolve())
    except (OSError, ValueError):
        return False
    return True


def payload_is_inside_active_workdir(
    payload: dict[str, Any], active_runs: dict[str, dict[str, Any]]
) -> bool:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return False
    for entry in active_runs.values():
        workdir = entry.get("workdir")
        if isinstance(workdir, str) and workdir.strip() and path_is_inside(cwd, workdir):
            return True
    return False


def current_branch(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=NO_MISTAKES_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def normalize_no_mistakes_value(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def toon_mapping(output: str, mapping_name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    in_mapping = False
    for line in output.splitlines():
        stripped = line.strip()
        if not line[:1].isspace():
            if stripped == f"{mapping_name}:":
                in_mapping = True
                continue
            if in_mapping:
                break
        if not in_mapping:
            continue
        key, separator, value = stripped.partition(":")
        if separator:
            values[normalize_no_mistakes_value(key)] = value.strip().lower()
    return values


def toon_rows(output: str, table_name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    columns: list[str] | None = None
    for line in output.splitlines():
        header = TOON_TABLE_HEADER_RE.match(line.strip())
        if header:
            if normalize_no_mistakes_value(header.group("name")) == table_name:
                columns = [
                    normalize_no_mistakes_value(column)
                    for column in header.group("columns").split(",")
                ]
            else:
                columns = None
            continue
        if columns is None or not line.strip() or ":" in line:
            continue
        values = [value.strip().lower() for value in line.split(",")]
        if len(values) == len(columns):
            rows.append(dict(zip(columns, values)))
    return rows


def no_mistakes_owns_branch(output: str, branch: str) -> bool:
    run = toon_mapping(output, "run")
    if run.get("branch") != branch.strip().lower():
        return False
    for step in toon_rows(output, "steps"):
        name = normalize_no_mistakes_value(step.get("step", ""))
        status = normalize_no_mistakes_value(step.get("status", ""))
        if name == "review" and status == "running":
            return True
        if name and name not in POST_PUBLICATION_STEPS and status in NO_MISTAKES_FIX_OWNERSHIP_STATES:
            return True
    return False


def no_mistakes_owns_current_branch(payload: dict[str, Any]) -> bool:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return False
    branch = current_branch(cwd)
    if branch is None:
        return False
    try:
        result = subprocess.run(
            ["no-mistakes", "axi", "status"],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=NO_MISTAKES_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return no_mistakes_owns_branch(result.stdout, branch)


def should_nudge_pre_bash(payload: dict[str, Any], home: Path) -> bool:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return False
    if "ringer.py" in command or "/ringer " in command:
        return False
    if not (PROVIDER_RE.search(command) or HARNESS_RE.search(command)):
        return False
    if no_mistakes_owns_current_branch(payload):
        return False

    active_runs = read_live_active_runs(home)
    if payload_is_inside_active_workdir(payload, active_runs):
        return False
    if command_references_active_workdir(command, active_runs):
        return False
    return True


def load_stdin_payload() -> dict[str, Any] | None:
    text = sys.stdin.read()
    payload = json.loads(text)
    return payload if isinstance(payload, dict) else None


def run(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] != "pre-bash":
        return 0
    payload = load_stdin_payload()
    if payload is None:
        return 0

    home = ringer_home()
    if should_nudge_pre_bash(payload, home) and claim_dedupe_marker(
        home, payload.get("session_id")
    ):
        output_nudge()
    return 0


def main() -> int:
    try:
        return run(sys.argv)
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
