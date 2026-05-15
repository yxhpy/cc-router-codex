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
    (source / ".claude" / "scripts" / "hook_intercept_create.py").write_text("print('pre')\n", encoding="utf-8")
    (source / ".claude" / "scripts" / "hook_user_prompt_submit.py").write_text("print('prompt')\n", encoding="utf-8")
    (source / ".claude" / "task-plans" / "route-cache.json").write_text("{}", encoding="utf-8")
    (source / ".claude" / "artifacts" / "old.txt").write_text("runtime", encoding="utf-8")
    (source / ".claude" / ".env.example").write_text(
        "\n".join(
            [
                "TASKCTL_ROUTER_PROVIDER=openai",
                "TASKCTL_ROUTER_CODEX_MODEL=gpt-5.4-mini",
                "TASKCTL_INPUT_GUARD_PROVIDER=openai",
                "ASSETGEN_CODEX_TIMEOUT=900",
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
                }
            }
        ),
        encoding="utf-8",
    )
    (source / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
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
            self.assertFalse((target / ".claude" / "task-plans" / "route-cache.json").exists())
            self.assertFalse((target / ".claude" / "artifacts" / "old.txt").exists())
            self.assertTrue((target / ".claude" / "task-plans").is_dir())
            env_text = (target / ".claude" / ".env").read_text(encoding="utf-8")
            self.assertIn("TASKCTL_INSTALL_OS=windows", env_text)
            self.assertIn("TASKCTL_PYTHON=C:/Python/python.exe", env_text)
            self.assertIn("CODEX_BIN=C:/npm/codex.cmd", env_text)
            self.assertIn("TASKCTL_ROUTER_PROVIDER=codex", env_text)

            settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
            command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            self.assertEqual(command, "C:/Python/python.exe .claude/scripts/hook_session_start.py")

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
