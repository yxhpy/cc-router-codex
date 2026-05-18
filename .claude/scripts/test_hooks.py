#!/usr/bin/env python3
"""Tests for Claude hook scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "scripts" / "hook_intercept_create.py"
PROMPT_HOOK = ROOT / ".claude" / "scripts" / "hook_user_prompt_submit.py"
STOP_HOOK = ROOT / ".claude" / "scripts" / "hook_stop_focus.py"
MAINTENANCE_MARKER = ROOT / ".claude" / "ALLOW_CONTROL_PLANE_WRITES"
FOCUS_STATE = ROOT / ".claude" / "task-plans" / "focus_state.json"


def display_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


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


def bash_guard_mock(*, allow: bool, direct_file_write: bool = False, reason: str = "mocked Bash guard") -> dict[str, str]:
    return {
        "TASKCTL_BASH_GUARD": "llm",
        "TASKCTL_BASH_GUARD_PROVIDER": "codex",
        "TASKCTL_BASH_GUARD_MOCK_JSON": json.dumps(
            {
                "allow": allow,
                "direct_file_write": direct_file_write,
                "reason": reason,
                "confidence": 0.95,
            },
            ensure_ascii=False,
        ),
    }


class HookTests(unittest.TestCase):
    def tearDown(self) -> None:
        if MAINTENANCE_MARKER.exists():
            MAINTENANCE_MARKER.unlink()
        if FOCUS_STATE.exists():
            FOCUS_STATE.unlink()

    def test_settings_hook_commands_resolve_inside_control_plane(self) -> None:
        settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(settings["permissions"]["defaultMode"], "bypassPermissions")
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["matcher"], "")
        commands = [
            hook["command"]
            for hook_group in settings["hooks"].values()
            for entry in hook_group
            for hook in entry["hooks"]
        ]

        for command in commands:
            _, _, script = command.partition(" ")
            path = Path(script)
            if path.is_absolute():
                self.assertTrue(
                    path.resolve(strict=False).is_relative_to((ROOT / ".claude" / "scripts").resolve(strict=False)),
                    command,
                )
            else:
                self.assertTrue(script.startswith(".claude/scripts/"), command)

    def test_blocks_direct_write_outside_claude(self) -> None:
        code, output = run_hook(HOOK, {"tool_name": "Write", "tool_input": {"file_path": "sample-page.html"}})

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")
        self.assertIn("taskctl.py capability", output["reason"])
        self.assertIn("taskctl.py", output["next_command"])
        self.assertIn(" command capability ", output["next_command"])
        self.assertIn("taskctl.py", output["replacement_command"])
        self.assertIn(" capability ", output["replacement_command"])
        self.assertIn("taskctl.py", output["command_contract"])
        self.assertIn(" command capability ", output["command_contract"])

    def test_allows_runtime_write_inside_claude(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/artifacts/job-1/report.md"}},
        )

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_grok_allows_task_plan_prompt_file_in_target_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "demo"
            prompt_file = workspace / ".claude" / "task-plans" / "uiux-prompt.txt"
            code, output = run_hook(
                HOOK,
                {
                    "hookEventName": "pre_tool_use",
                    "workspaceRoot": str(workspace),
                    "toolName": "write",
                    "toolInput": {"path": str(prompt_file), "content": "Long worker prompt"},
                },
                {"GROK_HOOK_EVENT": "pre_tool_use"},
            )

        self.assertEqual(code, 0)
        self.assertEqual(output["decision"], "allow")
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
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "node build.js"}},
            {"TASKCTL_BASH_GUARD": "off"},
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_allows_ambiguous_bash_when_model_guard_allows(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "node build.js"}},
            bash_guard_mock(allow=True),
        )

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_blocks_ambiguous_bash_when_model_guard_blocks(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "node build.js"}},
            bash_guard_mock(allow=False, direct_file_write=True, reason="would write files"),
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")
        self.assertIn("gpt-5.4-mini guard", output["reason"])
        self.assertIn("would write files", output["reason"])

    def test_allows_package_manager_project_commands(self) -> None:
        examples = [
            "npm install",
            "npm run build",
            "npm test",
            "pnpm install",
            "pnpm run dev",
            "yarn install",
            "yarn test",
            "bun install",
            "bun run build",
        ]
        for command in examples:
            with self.subTest(command=command):
                code, output = run_hook(HOOK, {"tool_name": "Bash", "tool_input": {"command": command}})
                self.assertEqual(code, 0)
                self.assertTrue(output["continue"])

    def test_blocks_package_manager_output_redirection(self) -> None:
        code, output = run_hook(HOOK, {"tool_name": "Bash", "tool_input": {"command": "npm install > install.log"}})

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_allows_swift_arrow_inside_quoted_taskctl_prompt(self) -> None:
        command = (
            'python .claude/scripts/taskctl.py capability --role fullstack '
            '--title "Implement Swift helper" '
            '--prompt "Implement func makeView() -> some View without changing behavior. '
            'Document why Set-Content and echo hi > file are unsafe shell examples." '
            '--artifact implementation_summary:.claude/artifacts/implementation_summary.md '
            '--workspace "/tmp/swift-app" --goal "Implement Swift helper"'
        )
        code, output = run_hook(HOOK, {"tool_name": "Bash", "tool_input": {"command": command}})

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_blocks_file_redirection_without_space(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "python .claude/scripts/taskctl.py status 1>status.txt"}},
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_blocks_ampersand_file_redirection(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "python .claude/scripts/taskctl.py status 1 &> status.txt"}},
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")

    def test_allows_null_device_redirection_for_readonly_discovery(self) -> None:
        command = (
            'which taskctl.py || where taskctl.py || find /c/Users -name "taskctl.py" 2>/dev/null | head -5; '
            'python -c "import sys; print(sys.executable)" 2>/dev/null; '
            'ls "C:\\Users\\Administrator" >NUL'
        )
        code, output = run_hook(HOOK, {"tool_name": "Bash", "tool_input": {"command": command}})

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_blocks_stderr_redirection_to_real_file(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "python .claude/scripts/taskctl.py status 2>error.log"}},
        )

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

    def test_allows_readonly_python_inline_probe(self) -> None:
        command = (
            "/c/Users/Administrator/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe -c '\n"
            "import sys\n"
            "sys.path.insert(0, \"C:/Users/Administrator/.grok/.claude/scripts\")\n"
            "import task_input_filter as tif\n"
            "print(tif.__file__)\n"
            "'"
        )
        code, output = run_hook(HOOK, {"tool_name": "Bash", "tool_input": {"command": command}})

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_allows_readonly_python_inline_probe_with_typo_to_fail_in_shell(self) -> None:
        command = (
            "/c/Users/Administrator/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe -c '\n"
            "import sys\n"
            "sys.path.insert(0, \"C:/Users/Administrator/.grok/.claude/scripts\")\n"
            "import task_input_filter as tif\n"
            "prin(\n"
            "'"
        )
        code, output = run_hook(HOOK, {"tool_name": "Bash", "tool_input": {"command": command}})

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_blocks_inline_python_file_write(self) -> None:
        code, output = run_hook(
            HOOK,
            {"tool_name": "Bash", "tool_input": {"command": "python -c \"from pathlib import Path; Path('x.txt').write_text('x')\""}},
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

    def test_allows_installed_absolute_python_taskctl_command(self) -> None:
        taskctl = ROOT / ".claude" / "scripts" / "taskctl.py"
        code, output = run_hook(
            HOOK,
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f'"C:\\Program Files\\Python312\\python.exe" "{taskctl}" status 1'
                },
            },
        )

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_allows_macos_absolute_versioned_python_taskctl_command(self) -> None:
        taskctl = ROOT / ".claude" / "scripts" / "taskctl.py"
        code, output = run_hook(
            HOOK,
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f'"/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13" "{taskctl}" status 1'
                },
            },
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
        self.assertIn(display_path(ROOT / ".claude" / "scripts" / "taskctl.py"), context)
        self.assertIn(display_path(ROOT / ".claude" / "scripts" / "focus_guard.py"), context)
        self.assertIn("Router source: mock", context)
        self.assertIn('--artifact "html:sample-page.html"', context)
        self.assertIn("--route-token", context)
        self.assertIn("Hard focus rule", context)
        self.assertTrue(FOCUS_STATE.exists())
        focus = json.loads(FOCUS_STATE.read_text(encoding="utf-8"))
        self.assertEqual(focus["status"], "active")
        self.assertEqual(focus["route"]["role"], "fullstack")
        self.assertNotIn("python .claude/scripts/taskctl.py capability", context)
        self.assertNotIn("python .claude/scripts/focus_guard.py", context)
        self.assertNotIn("filter-input --role", context)
        self.assertNotIn("enqueue <job_id>", context)

    def test_user_prompt_can_suggest_fast_async_capability(self) -> None:
        code, output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Build a fast smoke page."},
            {
                **router_mock(artifacts=["html:sample-page.html"]),
                "TASKCTL_INTERACTIVE_SPEED_PROFILE": "fast",
                "TASKCTL_INTERACTIVE_ASYNC": "1",
            },
        )

        self.assertEqual(code, 0)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("--speed-profile fast", context)
        self.assertIn("--async", context)

    def test_user_prompt_ignores_background_task_notifications(self) -> None:
        notification = """<task-notification>
<task-id>abc123</task-id>
<status>completed</status>
<summary>Background command completed (exit code 0)</summary>
</task-notification>"""

        code, output = run_hook(
            PROMPT_HOOK,
            {"prompt": notification, "cwd": str(ROOT)},
            router_mock(artifacts=["html:sample-page.html"]),
        )

        self.assertEqual(code, 0)
        self.assertEqual(output, {"continue": True})
        self.assertFalse(FOCUS_STATE.exists())

    def test_stop_hook_blocks_active_focus_until_complete_or_exhausted(self) -> None:
        code, _output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Create a focused smoke artifact.", "cwd": str(ROOT)},
            router_mock(artifacts=["test_report:.claude/artifacts/focus-smoke.md"]),
        )
        self.assertEqual(code, 0)

        blocked_code, blocked = run_hook(STOP_HOOK, {"cwd": str(ROOT)})
        self.assertEqual(blocked_code, 2)
        self.assertEqual(blocked["decision"], "block")
        self.assertFalse(blocked["continue"])
        self.assertIn("FOCUS_GUARD_BLOCK", blocked["reason"])
        self.assertIn("FOCUS_GUARD_BLOCK", blocked["stopReason"])
        self.assertIn("FOCUS_GUARD_BLOCK", blocked["systemMessage"])
        self.assertIn(display_path(ROOT / ".claude" / "scripts" / "focus_guard.py"), blocked["reason"])
        self.assertNotIn("python .claude/scripts/focus_guard.py", blocked["reason"])
        self.assertNotIn("hookSpecificOutput", blocked)

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / ".claude" / "scripts" / "focus_guard.py"),
                "complete",
                "--workspace",
                str(ROOT),
                "--evidence",
                "test artifact exists",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        allowed_code, allowed = run_hook(STOP_HOOK, {"cwd": str(ROOT)})
        self.assertEqual(allowed_code, 0)
        self.assertTrue(allowed["continue"])

    def test_grok_stop_hook_reports_active_focus_without_exit_2(self) -> None:
        code, _output = run_hook(
            PROMPT_HOOK,
            {"prompt": "Create a focused smoke artifact.", "cwd": str(ROOT)},
            router_mock(artifacts=["test_report:.claude/artifacts/focus-smoke.md"]),
        )
        self.assertEqual(code, 0)

        blocked_code, blocked = run_hook(
            STOP_HOOK,
            {"hookEventName": "stop", "workspaceRoot": str(ROOT)},
            {"GROK_HOOK_EVENT": "stop"},
        )

        self.assertEqual(blocked_code, 0)
        self.assertEqual(blocked["decision"], "allow")
        self.assertTrue(blocked["continue"])
        self.assertIn("GROK_STOP_NOTICE", blocked["reason"])
        self.assertIn("FOCUS_GUARD_BLOCK", blocked["reason"])

    def test_grok_pretooluse_bash_payload_blocks_with_deny_decision(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "hookEventName": "pre_tool_use",
                "workspaceRoot": str(ROOT),
                "toolName": "run_terminal_cmd",
                "toolInput": {"command": "echo hello > product.txt"},
            },
            {"GROK_HOOK_EVENT": "pre_tool_use"},
        )

        self.assertEqual(code, 0)
        self.assertEqual(output["decision"], "deny")
        self.assertTrue(output["continue"])
        self.assertIn("Bash file operation blocked", output["reason"])
        self.assertNotIn("hookSpecificOutput", output)
        self.assertNotIn("stopReason", output)

    def test_grok_pretooluse_run_terminal_command_alias_blocks_with_deny_decision(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "hookEventName": "pre_tool_use",
                "workspaceRoot": str(ROOT),
                "toolName": "run_terminal_command",
                "toolInput": {"command": "echo hello > product.txt"},
            },
            {"GROK_HOOK_EVENT": "pre_tool_use"},
        )

        self.assertEqual(code, 0)
        self.assertEqual(output["decision"], "deny")
        self.assertTrue(output["continue"])
        self.assertIn("Bash file operation blocked", output["reason"])

    def test_grok_pretooluse_claude_style_run_terminal_command_alias_is_nonfatal(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "workspaceRoot": str(ROOT),
                "tool_name": "run_terminal_command",
                "tool_input": {
                    "command": "python -c \"import json; print(json.load(open('data/sample.json')))\""
                },
            },
        )

        self.assertEqual(code, 0)
        self.assertEqual(output["decision"], "deny")
        self.assertTrue(output["continue"])
        self.assertIn("Inline Python workspace file operation blocked", output["reason"])
        self.assertNotIn("hookSpecificOutput", output)
        self.assertNotIn("stopReason", output)

    def test_grok_pretooluse_inline_python_open_blocks_without_stopping_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "demo"
            command = 'python -c "import json; print(json.load(open(\\\'data/sample.json\\\')))"'
            code, output = run_hook(
                HOOK,
                {
                    "hookEventName": "pre_tool_use",
                    "workspaceRoot": str(workspace),
                    "toolName": "run_terminal_command",
                    "toolInput": {"command": command},
                },
                {"GROK_HOOK_EVENT": "pre_tool_use"},
            )

        self.assertEqual(code, 0)
        self.assertEqual(output["decision"], "deny")
        self.assertTrue(output["continue"])
        self.assertIn("Inline Python workspace file operation blocked", output["reason"])
        self.assertIn("Do not read, process, or write target workspace files directly", output["reason"])
        self.assertIn("--role <role>", output["replacement_command"])

    def test_inline_python_path_read_text_blocks_before_bash_guard(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "python -c \"from pathlib import Path; print(Path('data/sample.json').read_text())\""
                },
            },
            bash_guard_mock(allow=True, reason="would otherwise allow readonly Python"),
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")
        self.assertIn("Inline Python workspace file operation blocked", output["reason"])

    def test_composite_inline_python_path_read_text_blocks_before_bash_guard(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "cd /c/Users/Administrator/Desktop/demo; python -c \"from pathlib import Path; print(Path('data/search-index.json').read_text())\""
                },
            },
            bash_guard_mock(allow=True, reason="would otherwise allow composite readonly Python"),
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["decision"], "block")
        self.assertIn("Inline Python workspace file operation blocked", output["reason"])

    def test_composite_readonly_python_probe_is_allowed(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "tool_name": "Bash",
                "tool_input": {"command": "cd .; python -c \"import sys; print(sys.executable)\" 2>/dev/null"},
            },
        )

        self.assertEqual(code, 0)
        self.assertTrue(output["continue"])

    def test_grok_pretooluse_write_payload_blocks_with_deny_decision(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "hookEventName": "pre_tool_use",
                "workspaceRoot": str(ROOT),
                "toolName": "write",
                "toolInput": {"path": "sample-page.html", "content": "<html></html>"},
            },
            {"GROK_HOOK_EVENT": "pre_tool_use"},
        )

        self.assertEqual(code, 0)
        self.assertEqual(output["decision"], "deny")
        self.assertTrue(output["continue"])
        self.assertIn("sample-page.html", output["reason"])
        self.assertNotIn("hookSpecificOutput", output)
        self.assertNotIn("stopReason", output)

    @unittest.skipIf(os.name != "nt", "MSYS drive path normalization is Windows-specific")
    def test_grok_pretooluse_write_payload_normalizes_msys_drive_path(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "hookEventName": "pre_tool_use",
                "workspaceRoot": "/c/Users/Administrator/Desktop/demo3",
                "toolName": "write",
                "toolInput": {
                    "path": "/c/Users/Administrator/Desktop/demo3/index.html",
                    "content": "<html></html>",
                },
            },
            {"GROK_HOOK_EVENT": "pre_tool_use"},
        )

        self.assertEqual(code, 0)
        self.assertEqual(output["decision"], "deny")
        self.assertIn("index.html", output["reason"])
        self.assertIn("C:\\Users\\Administrator\\Desktop\\demo3", output["reason"])
        self.assertNotIn("C:/c/Users/Administrator/Desktop/demo3/index.html", output["reason"])

    def test_grok_pretooluse_search_replace_payload_blocks_with_deny_decision(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "hookEventName": "pre_tool_use",
                "workspaceRoot": str(ROOT),
                "toolName": "search_replace",
                "toolInput": {"file_path": "sample-page.html"},
            },
            {"GROK_HOOK_EVENT": "pre_tool_use"},
        )

        self.assertEqual(code, 0)
        self.assertEqual(output["decision"], "deny")
        self.assertTrue(output["continue"])
        self.assertIn("sample-page.html", output["reason"])
        self.assertNotIn("hookSpecificOutput", output)
        self.assertNotIn("stopReason", output)

    def test_grok_pretooluse_claude_style_search_replace_alias_is_nonfatal(self) -> None:
        code, output = run_hook(
            HOOK,
            {
                "workspaceRoot": str(ROOT),
                "tool_name": "search_replace",
                "tool_input": {"file_path": "sample-page.html"},
            },
        )

        self.assertEqual(code, 0)
        self.assertEqual(output["decision"], "deny")
        self.assertTrue(output["continue"])
        self.assertIn("sample-page.html", output["reason"])
        self.assertNotIn("hookSpecificOutput", output)
        self.assertNotIn("stopReason", output)

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

    def test_user_prompt_auto_initializes_project_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target_workspace = Path(tmp) / "fresh-project"
            code, output = run_hook(
                PROMPT_HOOK,
                {"prompt": "Build a single-page sample prototype.", "cwd": str(target_workspace)},
                router_mock(artifacts=["html:sample-page.html"]),
            )

            self.assertEqual(code, 0)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn(f"Target workspace: {target_workspace.resolve()}", context)
            self.assertTrue((target_workspace / ".claude" / "cc-router-project.json").is_file())
            self.assertTrue((target_workspace / ".claude" / ".env").is_file())
            self.assertTrue((target_workspace / ".claude" / "task-plans" / "route-cache.json").is_file())
            self.assertFalse((target_workspace / ".claude" / "scripts" / "taskctl.py").exists())

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
