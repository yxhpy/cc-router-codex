#!/usr/bin/env python3
"""Tests for Claude hook scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "scripts" / "hook_intercept_create.py"
PROMPT_HOOK = ROOT / ".claude" / "scripts" / "hook_user_prompt_submit.py"
MAINTENANCE_MARKER = ROOT / ".claude" / "ALLOW_CONTROL_PLANE_WRITES"


def run_hook(path: Path, payload: dict[str, object], env: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        [sys.executable, str(path)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=merged_env,
    )
    output = json.loads(result.stdout)
    return result.returncode, output


def router_mock(
    *,
    production_work: bool = True,
    role: str = "fullstack",
    artifacts: list[str] | None = None,
    title: str = "Mock routed task",
    steps: list[dict[str, object]] | None = None,
) -> dict[str, str]:
    return {
        "TASKCTL_ROUTER_MOCK_JSON": json.dumps(
            {
                "production_work": production_work,
                "role": role,
                "title": title,
                "worker_prompt": f"Execute one atomic {role} task and stop.",
                "artifacts": artifacts or [],
                "steps": steps or [],
                "reason": "mocked LLM route",
                "confidence": 0.91,
            },
            ensure_ascii=False,
        )
    }


class HookTests(unittest.TestCase):
    def tearDown(self) -> None:
        if MAINTENANCE_MARKER.exists():
            MAINTENANCE_MARKER.unlink()

    def test_settings_hook_commands_are_repo_relative(self) -> None:
        settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        commands = [
            hook["command"]
            for hook_group in settings["hooks"].values()
            for entry in hook_group
            for hook in entry["hooks"]
        ]

        for command in commands:
            _, _, script = command.partition(" ")
            self.assertFalse(Path(script).is_absolute(), command)
            self.assertTrue(script.startswith(".claude/scripts/"), command)

    def test_blocks_direct_write_outside_claude(self) -> None:
        code, output = run_hook(HOOK, {"tool_name": "Write", "tool_input": {"file_path": "sample-page.html"}})

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")
        self.assertIn("taskctl.py capability", output["reason"])

    def test_allows_runtime_write_inside_claude(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/artifacts/job-1/report.md"}},
        )

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_blocks_control_plane_write_without_maintenance(self) -> None:
        code, output = run_hook(HOOK, {"tool_name": "Write", "tool_input": {"file_path": ".claude/settings.json"}})

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")
        self.assertIn("maintenance", output["reason"])

    def test_allows_control_plane_write_with_maintenance_env(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/settings.json"}},
            {"CLAUDE_CONTROL_PLANE_WRITE": "1"},
        )

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_marker_requires_future_expiry_to_allow_control_plane_write(self) -> None:
        expired = datetime.now(timezone.utc) - timedelta(minutes=1)
        MAINTENANCE_MARKER.write_text(json.dumps({"expires_at": expired.isoformat()}), encoding="utf-8")

        code, output = run_hook(HOOK, {"tool_name": "Write", "tool_input": {"file_path": ".claude/settings.json"}})
        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        MAINTENANCE_MARKER.write_text(json.dumps({"expires_at": future.isoformat()}), encoding="utf-8")
        code, output = run_hook(HOOK, {"tool_name": "Write", "tool_input": {"file_path": ".claude/settings.json"}})
        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_blocks_direct_design_reference_write(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/design-references/example/DESIGN.md"}},
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")
        self.assertIn("sync_design_refs.py", output["reason"])

    def test_blocks_powershell_file_creation(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "Set-Content -Path sample-page.html -Value '<html></html>'"}},
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_blocks_powershell_download_writes(self) -> None:
        examples = [
            "Invoke-WebRequest https://example.com/a.zip -OutFile a.zip",
            "Start-BitsTransfer -Source https://example.com/a.zip -Destination a.zip",
            "Export-Csv -Path out.csv",
        ]
        for command in examples:
            with self.subTest(command=command):
                code, output = run_hook(HOOK, {"tool_name": "Bash", "tool_input": {"command": command}})
                self.assertEqual(code, 2)
                self.assertEqual(output["decision"], "block")

    def test_blocks_unknown_bash_by_default(self) -> None:
        code, output = run_hook(HOOK, {"tool_name": "Bash", "tool_input": {"command": "node build.js"}})

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_blocks_multiedit_outside_claude(self) -> None:
        code, output = run_hook(HOOK, {"tool_name": "MultiEdit", "tool_input": {"file_path": "src/app.js"}})

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_blocks_direct_task_tool(self) -> None:
        code, output = run_hook(HOOK, {"tool_name": "Task", "tool_input": {"description": "build project"}})

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_blocks_direct_codex_exec(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "codex exec --sandbox workspace-write 'build project'"}},
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_blocks_inline_sqlite_mutation(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "python -c \"import sqlite3; conn=sqlite3.connect('.claude/taskctl.sqlite3'); conn.execute('UPDATE tasks SET status=\\'queued\\' WHERE id=1')\""
                },
            },
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_allows_taskctl_command(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "python .claude/scripts/taskctl.py status 1"}},
        )

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_allows_taskctl_help_with_stderr_pipe(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": 'python ".claude/scripts/taskctl.py" --help 2>&1 | head -30'}},
        )

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_allows_absolute_taskctl_command(self) -> None:
        taskctl = ROOT / ".claude" / "scripts" / "taskctl.py"
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": f'python "{taskctl}" status 1'}},
        )

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_blocks_taskctl_stdout_file_redirect(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "python .claude/scripts/taskctl.py status 1 > status.txt"}},
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_blocks_taskctl_pipe_to_tee(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "python .claude/scripts/taskctl.py status 1 | tee status.txt"}},
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_user_prompt_routes_production_work_to_atomic_control_plane(self) -> None:
        code, output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Build a high-fidelity sample page using HTML, CSS, and JavaScript."},
            router_mock(artifacts=["html:sample-page.html"]),
        )

        self.assertEqual(code, 0)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("taskctl.py capability", context)
        self.assertIn("Router source: mock", context)
        self.assertIn('--artifact "html:sample-page.html"', context)
        self.assertIn("--route-token", context)
        self.assertNotIn("filter-input --role", context)
        self.assertNotIn("enqueue <job_id>", context)

    def test_user_prompt_uses_payload_cwd_as_target_workspace(self) -> None:
        target_workspace = (ROOT.parent / "target-workspace").resolve()
        code, output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Build a single-page sample prototype.", "cwd": str(target_workspace)},
            router_mock(artifacts=["html:sample-page.html"]),
        )

        self.assertEqual(code, 0)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"Target workspace: {target_workspace}", context)
        self.assertIn(f'--workspace "{target_workspace}"', context)
        self.assertNotIn(f'--workspace "{ROOT}"', context)

    def test_block_guidance_uses_payload_cwd_as_target_workspace(self) -> None:
        target_workspace = (ROOT.parent / "target-workspace").resolve()
        code, output = run_hook(
            HOOK,
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "sample-page.html"},
                "cwd": str(target_workspace),
            },
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")
        self.assertIn(f'--workspace "{target_workspace}"', output["reason"])
        self.assertNotIn(f'--workspace "{ROOT}"', output["reason"])

    def test_user_prompt_notes_fixed_workflows_are_legacy_templates(self) -> None:
        code, output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Build a single-page reservation flow using HTML, CSS, and JavaScript."},
            router_mock(artifacts=["html:reservation-page.html"]),
        )

        self.assertEqual(code, 0)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Do not use fixed workflows", context)
        self.assertIn("open-license/project media", context)
        self.assertIn("generated local raster assets", context)
        self.assertIn("no SVG generated-asset fallback", context)
        self.assertIn("local_asset_manifest", context)

    def test_user_prompt_routes_file_target_without_help_me_prefix(self) -> None:
        code, output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Create a sample listing page and save it as sample-page.html."},
            router_mock(artifacts=["html:sample-page.html"]),
        )

        self.assertEqual(code, 0)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("taskctl.py capability", context)
        self.assertIn("Do not directly write/edit", context)
        self.assertIn("--role fullstack", context)
        self.assertIn('--artifact "html:sample-page.html"', context)

    def test_user_prompt_can_route_standalone_image_assets_to_assetgen(self) -> None:
        code, output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Generate a game asset icon and save it locally."},
            router_mock(role="assetgen", artifacts=["image:assets/generated/game-icon.png"]),
        )

        self.assertEqual(code, 0)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("--role assetgen", context)
        self.assertIn('--artifact "image:assets/generated/game-icon.png"', context)
        self.assertIn("assetgen is image-asset-only", context)

    def test_user_prompt_can_suggest_role_composition_before_implementation(self) -> None:
        steps = [
            {
                "role": "uiux",
                "title": "Select traceable sample page style",
                "worker_prompt": "Use project design sources or .claude/design-references and record design_reference_selection plus style_contract.",
                "artifacts": [
                    "design_reference_selection:.claude/artifacts/design_reference_selection.md",
                    "style_contract:.claude/artifacts/style_contract.md",
                ],
                "purpose": "avoid model-invented frontend style",
            },
            {
                "role": "fullstack",
                "title": "Implement sample-page.html",
                "worker_prompt": "Implement sample-page.html from the style contract.",
                "artifacts": ["html:sample-page.html"],
            },
        ]
        code, output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Create a sample listing page and save it as sample-page.html."},
            router_mock(role="fullstack", artifacts=["html:sample-page.html"], steps=steps),
        )

        self.assertEqual(code, 0)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("LLM-suggested capability composition", context)
        self.assertIn("1. uiux: Select traceable sample page style", context)
        self.assertIn("2. fullstack: Implement sample-page.html", context)
        self.assertIn("--role uiux", context)
        self.assertIn('--artifact "style_contract:.claude/artifacts/style_contract.md"', context)
        self.assertIn("Do not enqueue the whole composition", context)
        self.assertIn(".claude/design-references", context)
        self.assertIn("asset_generation_brief", context)

    def test_user_prompt_routes_ui_feedback_without_explicit_action(self) -> None:
        code, output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Adjust the sample page visual layout."},
            router_mock(role="fullstack", artifacts=["html:sample-page.html"]),
        )

        self.assertEqual(code, 0)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("taskctl.py capability", context)
        self.assertIn("Recommended next tool call", context)

    def test_user_prompt_routes_backend_and_architecture_requests(self) -> None:
        examples = {
            "Implement a bounded backend endpoint for the current project.": "--role fullstack",
            "Create an architecture plan for the current project.": "--role planner",
            "Verify the current project behavior and write a test report.": "--role tester",
        }
        for prompt, role_hint in examples.items():
            with self.subTest(prompt=prompt):
                role = role_hint.replace("--role ", "")
                code, output = run_hook(PROMPT_HOOK, {"prompt": prompt}, router_mock(role=role))
                self.assertEqual(code, 0)
                context = output["hookSpecificOutput"]["additionalContext"]
                self.assertIn("taskctl.py capability", context)
                self.assertIn(role_hint, context)

    def test_user_prompt_allows_non_production_when_llm_says_no(self) -> None:
        code, output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Summarize the current status."},
            router_mock(production_work=False, role="closer"),
        )

        self.assertEqual(code, 0)
        self.assertEqual(output, {"continue": True})


if __name__ == "__main__":
    unittest.main()
