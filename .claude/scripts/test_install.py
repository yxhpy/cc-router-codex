#!/usr/bin/env python3
"""Unit tests for the project installer."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("cc_router_install", ROOT / "install.py")
installer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


def make_source(root: Path) -> Path:
    source = root / "source"
    (source / ".claude" / "scripts").mkdir(parents=True)
    (source / ".claude" / "task-plans").mkdir()
    (source / ".claude" / "artifacts").mkdir()
    (source / ".claude" / "scripts" / "hook_session_start.py").write_text("print('session')\n", encoding="utf-8")
    (source / ".claude" / "scripts" / "hook_stop_focus.py").write_text("print('stop')\n", encoding="utf-8")
    (source / ".claude" / "scripts" / "hook_intercept_create.py").write_text("print('pre')\n", encoding="utf-8")
    (source / ".claude" / "scripts" / "hook_user_prompt_submit.py").write_text("print('prompt')\n", encoding="utf-8")
    (source / ".claude" / "task-plans" / "route-cache.json").write_text("{}", encoding="utf-8")
    (source / ".claude" / "artifacts" / "old.txt").write_text("runtime", encoding="utf-8")
    (source / ".claude" / ".prompt-searcher").mkdir()
    (source / ".claude" / ".prompt-searcher" / "install.json").write_text("{}", encoding="utf-8")
    (source / ".claude" / ".env.example").write_text(
        "\n".join(
            [
                "TASKCTL_ROUTER_PROVIDER=openai",
                "TASKCTL_ROUTER_CODEX_MODEL=gpt-5.4-mini",
                "TASKCTL_INPUT_GUARD_PROVIDER=openai",
                "ASSETGEN_CODEX_MODEL=gpt-5.4-mini",
                "ASSETGEN_CODEX_TIMEOUT=900",
                "ASSETGEN_PROMPT_MCP_TARGET=.prompt-searcher",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (source / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python .claude/scripts/hook_session_start.py",
                                }
                            ]
                        }
                    ],
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python .claude/scripts/hook_intercept_create.py",
                                }
                            ]
                        }
                    ],
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python .claude/scripts/hook_stop_focus.py",
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    (source / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
    (source / "CONTEXT.md").write_text("# Project Context\n", encoding="utf-8")
    (source / "docs" / "adr").mkdir(parents=True)
    (source / "docs" / "adr" / "2026-05-16-sample.md").write_text("# ADR\n", encoding="utf-8")
    (source / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (source / "VERSIONING.md").write_text("# Versioning\n", encoding="utf-8")
    return source


class InstallTests(unittest.TestCase):
    def test_installs_control_plane_and_generates_local_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_source(root)
            target = root / "target"
            detection = installer.Detection("windows", "C:/Python/python.exe", "C:/npm/codex.cmd")

            with mock.patch.object(installer, "detect_system", return_value=detection):
                result = installer.install_control_plane(source_root=source, target=target, yes=True)

            self.assertEqual(result.target, target.resolve())
            self.assertTrue((target / ".claude" / "scripts" / "hook_session_start.py").exists())
            self.assertTrue((target / "CLAUDE.md").exists())
            self.assertFalse((target / "CONTEXT.md").exists())
            self.assertFalse((target / "docs" / "adr").exists())
            self.assertTrue((target / "VERSION").exists())
            self.assertTrue((target / "VERSIONING.md").exists())
            self.assertFalse((target / ".claude" / "task-plans" / "route-cache.json").exists())
            self.assertFalse((target / ".claude" / "artifacts" / "old.txt").exists())
            self.assertFalse((target / ".claude" / ".prompt-searcher").exists())
            self.assertTrue((target / ".claude" / "task-plans").is_dir())
            env_text = (target / ".claude" / ".env").read_text(encoding="utf-8")
            self.assertIn("TASKCTL_INSTALL_OS=windows", env_text)
            self.assertIn("TASKCTL_PYTHON=C:/Python/python.exe", env_text)
            self.assertIn("CODEX_BIN=C:/npm/codex.cmd", env_text)
            self.assertIn("TASKCTL_ROUTER_PROVIDER=codex", env_text)
            self.assertIn("TASKCTL_BASH_GUARD=llm", env_text)
            self.assertIn("TASKCTL_BASH_GUARD_PROVIDER=codex", env_text)
            self.assertIn("TASKCTL_BASH_GUARD_CODEX_MODEL=gpt-5.4-mini", env_text)
            self.assertIn("ASSETGEN_CODEX_MODEL=gpt-5.4-mini", env_text)
            self.assertIn("ASSETGEN_PROMPT_MCP_TARGET=.prompt-searcher", env_text)
            self.assertIn("ASSETGEN_PROMPT_MCP_REPO=https://github.com/yxhpy/image-2-prompt", env_text)
            self.assertIn("ASSETGEN_PROMPT_MCP_VERSION_TIMEOUT=5", env_text)
            self.assertIn("ASSETGEN_PROMPT_MCP_LATEST_TTL_SECONDS=3600", env_text)

            settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
            command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            session_hook = installer.normalize_command_path(
                target.resolve() / ".claude" / "scripts" / "hook_session_start.py"
            )
            self.assertEqual(command, f"C:/Python/python.exe {session_hook}")
            stop_command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
            stop_hook = installer.normalize_command_path(
                target.resolve() / ".claude" / "scripts" / "hook_stop_focus.py"
            )
            self.assertEqual(stop_command, f"C:/Python/python.exe {stop_hook}")
            self.assertEqual(settings["permissions"]["defaultMode"], "bypassPermissions")
            allow = settings["permissions"]["allow"]
            self.assertIn("Bash(python *)", allow)
            self.assertIn("Bash(codex *)", allow)
            self.assertIn("Bash(C:/Python/python.exe *)", allow)
            self.assertIn("Bash(C:/npm/codex.cmd *)", allow)

    def test_installer_quotes_permission_rules_for_paths_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_source(root)
            target = root / "target"
            detection = installer.Detection(
                "windows",
                "C:/Program Files/Python/python.exe",
                "C:/Program Files/node/codex.cmd",
            )

            with mock.patch.object(installer, "detect_system", return_value=detection):
                installer.install_control_plane(source_root=source, target=target, yes=True)

            settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
            command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            allow = settings["permissions"]["allow"]
            session_hook = installer.normalize_command_path(
                target.resolve() / ".claude" / "scripts" / "hook_session_start.py"
            )
            self.assertEqual(command, f'"C:/Program Files/Python/python.exe" {session_hook}')
            self.assertIn('Bash("C:/Program Files/Python/python.exe" *)', allow)
            self.assertIn('Bash("C:/Program Files/node/codex.cmd" *)', allow)

    def test_windows_command_paths_are_normalized_for_bash(self) -> None:
        self.assertEqual(
            installer.normalize_command_path(r"C:\Users\alice\AppData\Local\Programs\Python\python.exe"),
            "C:/Users/alice/AppData/Local/Programs/Python/python.exe",
        )

    def test_existing_install_requires_y_before_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_source(root)
            target = root / "target"
            (target / ".claude").mkdir(parents=True)
            (target / ".claude" / "old.txt").write_text("old", encoding="utf-8")
            (target / "CLAUDE.md").write_text("old rules", encoding="utf-8")

            with self.assertRaises(SystemExit) as aborted:
                installer.install_control_plane(
                    source_root=source,
                    target=target,
                    yes=False,
                    input_func=lambda _prompt: "n",
                    output_func=lambda _line: None,
                )

            self.assertIn("ABORTED", str(aborted.exception))
            self.assertTrue((target / ".claude" / "old.txt").exists())

            installer.install_control_plane(
                source_root=source,
                target=target,
                yes=False,
                input_func=lambda _prompt: "y",
                output_func=lambda _line: None,
            )

            self.assertFalse((target / ".claude" / "old.txt").exists())
            self.assertEqual((target / "CLAUDE.md").read_text(encoding="utf-8"), "# Rules\n")
            self.assertEqual((target / "VERSION").read_text(encoding="utf-8"), "0.1.0\n")
            self.assertEqual((target / "VERSIONING.md").read_text(encoding="utf-8"), "# Versioning\n")

    def test_refuses_to_install_over_source_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = make_source(Path(tmp))
            with self.assertRaises(SystemExit) as rejected:
                installer.install_control_plane(source_root=source, target=source, yes=True)

        self.assertIn("target cannot be", str(rejected.exception))

    def test_remote_bootstrap_scripts_are_present(self) -> None:
        ps1 = (ROOT / "install.ps1").read_text(encoding="utf-8")
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("https://github.com/$Repo/archive/refs/heads/$Ref.zip", ps1)
        self.assertIn("install.py", ps1)
        self.assertIn("--target", ps1)
        self.assertIn("https://github.com/$REPO/archive/refs/heads/$REF.zip", sh)
        self.assertIn("install.py", sh)
        self.assertIn("--target", sh)


if __name__ == "__main__":
    unittest.main()
