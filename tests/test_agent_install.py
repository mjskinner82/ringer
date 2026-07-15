#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AgentInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.ringer_home = Path(self.tmp.name) / "ringer-home"
        self.home.mkdir()

    def run_cli(self, *args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["RINGER_HOME"] = str(self.ringer_home)
        return subprocess.run(
            [sys.executable, "ringer.py", *args],
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def read_settings(self, root: Path | None = None) -> dict[str, object]:
        base = self.home if root is None else root
        return json.loads((base / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def read_codex_hooks(self, root: Path | None = None) -> dict[str, object]:
        base = self.home if root is None else root
        return json.loads((base / ".codex" / "hooks.json").read_text(encoding="utf-8"))

    def ringer_handlers(self, settings: dict[str, object]) -> list[dict[str, object]]:
        handlers: list[dict[str, object]] = []
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            return handlers
        for groups in hooks.values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for handler in group.get("hooks", []):
                    if isinstance(handler, dict) and "ringer_nudge.py" in str(handler.get("command", "")):
                        handlers.append(handler)
        return handlers

    def test_fresh_install_creates_skill_copy_and_hook_entries(self) -> None:
        result = self.run_cli("install-agent")
        self.assertEqual(0, result.returncode, result.stderr)

        skill = self.home / ".claude" / "skills" / "ringer" / "SKILL.md"
        universal_skill = self.home / ".agents" / "skills" / "ringer" / "SKILL.md"
        reference = self.home / ".agents" / "skills" / "ringer" / "references" / "manifest-design.md"
        self.assertTrue(skill.exists())
        self.assertTrue(universal_skill.exists())
        self.assertTrue(reference.exists())
        self.assertEqual((ROOT / ".claude" / "skills" / "ringer" / "SKILL.md").read_text(), skill.read_text())
        self.assertEqual(skill.read_text(), universal_skill.read_text())
        self.assertEqual(
            (ROOT / ".claude" / "skills" / "ringer" / "references" / "manifest-design.md").read_text(),
            reference.read_text(),
        )

        hook_script = self.home / ".local" / "share" / "ringer" / "hooks" / "ringer_nudge.py"
        self.assertTrue(hook_script.exists())
        self.assertEqual((ROOT / "hooks" / "ringer_nudge.py").read_text(), hook_script.read_text())
        skill_text = skill.read_text(encoding="utf-8")
        self.assertIn("/Users/mattskinner/.local/bin/ringer status", skill_text)
        self.assertIn("launch-class failure", skill_text)

        settings = self.read_settings()
        hooks = settings["hooks"]
        self.assertIsInstance(hooks, dict)
        self.assertEqual("Bash", hooks["PreToolUse"][0]["matcher"])
        self.assertEqual("command", hooks["PreToolUse"][0]["hooks"][0]["type"])
        self.assertIn("ringer_nudge.py", hooks["PreToolUse"][0]["hooks"][0]["command"])
        self.assertTrue(hooks["PreToolUse"][0]["hooks"][0]["command"].endswith(" pre-bash"))
        self.assertEqual([], hooks.get("PostToolUse", []))

        codex_hooks = self.read_codex_hooks()["hooks"]
        self.assertEqual("Bash", codex_hooks["PreToolUse"][0]["matcher"])
        self.assertEqual([], codex_hooks.get("PostToolUse", []))
        self.assertIn(str(hook_script), codex_hooks["PreToolUse"][0]["hooks"][0]["command"])

    def test_second_install_is_idempotent(self) -> None:
        first = self.run_cli("install-agent")
        self.assertEqual(0, first.returncode, first.stderr)
        settings_before = self.read_settings()
        codex_before = self.read_codex_hooks()

        second = self.run_cli("install-agent")
        self.assertEqual(0, second.returncode, second.stderr)
        settings_after = self.read_settings()
        codex_after = self.read_codex_hooks()

        self.assertEqual(settings_before, settings_after)
        self.assertEqual(codex_before, codex_after)
        self.assertEqual(1, len(self.ringer_handlers(settings_after)))
        self.assertEqual(1, len(self.ringer_handlers(codex_after)))

    def test_install_removes_legacy_post_edit_hook_and_preserves_unrelated_handler(self) -> None:
        settings_path = self.home / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Edit|Write",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 /tmp/ringer_nudge.py post-edit",
                                    },
                                    {"type": "command", "command": "echo keep-post-hook"},
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_cli("install-agent")
        self.assertEqual(0, result.returncode, result.stderr)

        settings = self.read_settings()
        self.assertEqual(1, len(self.ringer_handlers(settings)))
        self.assertEqual(
            [{"type": "command", "command": "echo keep-post-hook"}],
            settings["hooks"]["PostToolUse"][0]["hooks"],
        )

    def test_install_migrates_legacy_checkout_hook_to_stable_path(self) -> None:
        legacy_command = "python3 /tmp/ringer/hooks/ringer_nudge.py pre-bash 2>/dev/null || true"
        settings_path = self.home / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": legacy_command}],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_cli("install-agent")
        self.assertEqual(0, result.returncode, result.stderr)

        groups = self.read_settings()["hooks"]["PreToolUse"]
        self.assertEqual(1, len(groups))
        command = groups[0]["hooks"][0]["command"]
        self.assertIn(str(self.home / ".local" / "share" / "ringer" / "hooks" / "ringer_nudge.py"), command)
        self.assertNotIn("/tmp/ringer/hooks", command)

    def test_install_migrates_multiple_stale_ringer_groups_to_one_canonical_group(self) -> None:
        settings_path = self.home / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 /tmp/old-checkout/hooks/ringer_nudge.py pre-bash",
                                    }
                                ],
                            },
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 /tmp/other-checkout/hooks/ringer_nudge.py pre-bash",
                                    }
                                ],
                            },
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_cli("install-agent")
        self.assertEqual(0, result.returncode, result.stderr)

        groups = self.read_settings()["hooks"]["PreToolUse"]
        self.assertEqual(1, len(groups))
        self.assertEqual("Bash", groups[0]["matcher"])
        self.assertEqual(1, len(groups[0]["hooks"]))
        command = groups[0]["hooks"][0]["command"]
        self.assertIn(str(self.home / ".local" / "share" / "ringer" / "hooks" / "ringer_nudge.py"), command)
        self.assertNotIn("/tmp/old-checkout", command)
        self.assertNotIn("/tmp/other-checkout", command)

    def test_install_strips_ringer_handler_from_mixed_group_without_leaving_empty_groups(self) -> None:
        settings_path = self.home / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {"type": "command", "command": "echo unrelated"},
                                    {
                                        "type": "command",
                                        "command": "python3 /tmp/old-checkout/hooks/ringer_nudge.py pre-bash",
                                    },
                                ],
                            },
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 /tmp/old-checkout/hooks/ringer_nudge.py pre-bash",
                                    },
                                    {
                                        "type": "command",
                                        "command": "python3 /tmp/other-checkout/hooks/ringer_nudge.py pre-bash",
                                    },
                                ],
                            },
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_cli("install-agent")
        self.assertEqual(0, result.returncode, result.stderr)

        groups = self.read_settings()["hooks"]["PreToolUse"]
        self.assertEqual(2, len(groups))
        for group in groups:
            self.assertNotEqual([], group["hooks"])
        self.assertEqual("Bash", groups[0]["matcher"])
        self.assertEqual([{"type": "command", "command": "echo unrelated"}], groups[0]["hooks"])
        ringer_commands = [handler["command"] for handler in self.ringer_handlers(self.read_settings())]
        pre_bash_commands = [command for command in ringer_commands if command.endswith(" pre-bash")]
        self.assertEqual(1, len(pre_bash_commands))
        self.assertIn(
            str(self.home / ".local" / "share" / "ringer" / "hooks" / "ringer_nudge.py"),
            pre_bash_commands[0],
        )

    def test_install_preserves_unrelated_hooks_and_settings_keys(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        settings_path = claude / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "echo unrelated",
                                    }
                                ],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

        result = self.run_cli("install-agent")
        self.assertEqual(0, result.returncode, result.stderr)

        settings = self.read_settings()
        self.assertEqual("dark", settings["theme"])
        pre_groups = settings["hooks"]["PreToolUse"]
        commands = [handler["command"] for group in pre_groups for handler in group["hooks"]]
        self.assertIn("echo unrelated", commands)
        self.assertEqual(1, len(list(claude.glob("settings.json.bak-*"))))

    def test_uninstall_removes_only_ringer_entries_and_skill_dir(self) -> None:
        install = self.run_cli("install-agent")
        self.assertEqual(0, install.returncode, install.stderr)
        settings_path = self.home / ".claude" / "settings.json"
        settings = self.read_settings()
        settings["hooks"]["PreToolUse"].append(
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": "echo keep-me",
                    }
                ],
            }
        )
        settings["custom"] = {"keep": True}
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

        uninstall = self.run_cli("uninstall-agent")
        self.assertEqual(0, uninstall.returncode, uninstall.stderr)

        after = self.read_settings()
        self.assertEqual({"keep": True}, after["custom"])
        self.assertEqual([], self.ringer_handlers(after))
        kept = [
            handler["command"]
            for group in after["hooks"]["PreToolUse"]
            for handler in group["hooks"]
        ]
        self.assertEqual(["echo keep-me"], kept)
        self.assertFalse((self.home / ".claude" / "skills" / "ringer").exists())
        self.assertFalse((self.home / ".agents" / "skills" / "ringer").exists())
        self.assertFalse((self.home / ".local" / "share" / "ringer" / "hooks" / "ringer_nudge.py").exists())
        self.assertEqual([], self.ringer_handlers(self.read_codex_hooks()))

    def test_project_variant_writes_under_temp_cwd(self) -> None:
        project = Path(self.tmp.name) / "project"
        project.mkdir()
        os.symlink(ROOT / "ringer.py", project / "ringer.py")

        install = self.run_cli("install-agent", "--project", cwd=project)
        self.assertEqual(0, install.returncode, install.stderr)
        self.assertTrue((project / ".claude" / "skills" / "ringer" / "SKILL.md").exists())
        self.assertTrue((project / ".agents" / "skills" / "ringer" / "SKILL.md").exists())
        self.assertTrue(
            (project / ".agents" / "skills" / "ringer" / "references" / "run-operations.md").exists()
        )
        self.assertTrue((project / ".agents" / "ringer" / "hooks" / "ringer_nudge.py").exists())
        self.assertTrue((project / ".claude" / "settings.json").exists())
        self.assertTrue((project / ".codex" / "hooks.json").exists())
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".codex").exists())

        uninstall = self.run_cli("uninstall-agent", "--project", cwd=project)
        self.assertEqual(0, uninstall.returncode, uninstall.stderr)
        self.assertFalse((project / ".claude" / "skills" / "ringer").exists())
        self.assertFalse((project / ".agents" / "skills" / "ringer").exists())
        self.assertFalse((project / ".agents" / "ringer" / "hooks" / "ringer_nudge.py").exists())
        settings = self.read_settings(project)
        self.assertEqual([], self.ringer_handlers(settings))
        self.assertEqual([], self.ringer_handlers(self.read_codex_hooks(project)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
