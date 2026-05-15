#!/usr/bin/env python3
"""Real Claude CLI integration checks for project hooks and taskctl routing.

These tests intentionally call the installed `claude` CLI. They are not part of
the cheap default unit suite because they make real model calls.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from project_paths import script_command


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / ".claude" / "artifacts"


def run_command(args: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        stderr += f"\nTIMEOUT after {timeout}s"
        return subprocess.CompletedProcess(args, 124, stdout, stderr)


def run_claude(name: str, args: list[str], *, timeout: int = 300, allow_nonzero: bool = False) -> list[dict[str, object]]:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    result = run_command(args, timeout=timeout)
    log_path = ARTIFACT_DIR / f"claude-cli-{name}.ndjson"
    log_path.write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
    if result.returncode != 0 and not allow_nonzero:
        raise AssertionError(f"claude scenario {name} failed exit={result.returncode}; log={log_path}")
    events: list[dict[str, object]] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not events:
        raise AssertionError(f"claude scenario {name} produced no JSON events; log={log_path}")
    return events


def event_text(events: list[dict[str, object]]) -> str:
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events)


def require_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle}")


def hook_payloads(events: list[dict[str, object]], hook_event: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for event in events:
        if event.get("hook_event") != hook_event or event.get("subtype") != "hook_response":
            continue
        output = str(event.get("output") or "")
        try:
            payloads.append(json.loads(output))
        except json.JSONDecodeError:
            continue
    return payloads


def require_hook_output_contains(events: list[dict[str, object]], hook_event: str, needle: str, label: str) -> None:
    for payload in hook_payloads(events, hook_event):
        if needle in json.dumps(payload, ensure_ascii=False):
            return
    raise AssertionError(f"missing {label} in {hook_event} hook output: {needle}")


def base_claude_args(*extra: str) -> list[str]:
    claude = shutil.which("claude")
    if not claude:
        raise AssertionError("claude CLI not found on PATH")
    return [
        claude,
        "-p",
        "--output-format",
        "stream-json",
        "--include-hook-events",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        *extra,
    ]


def check_version() -> None:
    result = run_command([shutil.which("claude") or "claude", "--version"], timeout=60)
    if result.returncode != 0 or "Claude Code" not in result.stdout:
        raise AssertionError(f"claude --version failed: {result.stdout}{result.stderr}")
    print(result.stdout.strip())


def check_smoke() -> None:
    events = run_claude(
        "smoke",
        base_claude_args(
            "--tools",
            "",
            "--max-budget-usd",
            "0.20",
            "Return exactly CLAUDE_CLI_SMOKE_OK",
        ),
    )
    text = event_text(events)
    require_contains(text, "SessionStart", "SessionStart hook event")
    require_contains(text, "UserPromptSubmit", "UserPromptSubmit hook event")
    require_contains(text, "CLAUDE_CLI_SMOKE_OK", "smoke response")


def check_routing_context() -> None:
    prompts = {
        "routing": "Build a high-fidelity sample page using HTML, CSS, and JavaScript.",
        "routing-file-target": "Create a sample listing page and save it as sample-page.html.",
    }
    for name, prompt in prompts.items():
        events = run_claude(
            name,
            base_claude_args(
                "--tools",
                "",
                "--max-budget-usd",
                "0.001",
                prompt,
            ),
            timeout=90,
            allow_nonzero=True,
        )
        require_hook_output_contains(events, "UserPromptSubmit", "taskctl.py capability", "atomic capability command")
        require_hook_output_contains(events, "UserPromptSubmit", "--artifact <kind:path>", "artifact binding guidance")
        require_hook_output_contains(events, "UserPromptSubmit", "Recommended next tool call", "concrete command guidance")
        if name == "routing-file-target":
            text = event_text(events)
            require_contains(text, '"name": "Bash"', "first action uses Bash/taskctl")
            if '"name": "Write"' in text:
                raise AssertionError("routing-file-target attempted Write instead of taskctl Bash")


def check_write_block() -> None:
    target = ROOT / "cli_blocked_write.txt"
    if target.exists():
        target.unlink()
    events = run_claude(
        "write-block",
        base_claude_args(
            "--tools",
            "Write",
            "--permission-mode",
            "bypassPermissions",
            "--max-budget-usd",
            "0.25",
            "--system-prompt",
            "You are a hook test harness. You must call the Write tool exactly once to create cli_blocked_write.txt with content CLAUDE_WRITE_BLOCK_TEST. Do not use bash. Do not use taskctl. After the tool call, stop.",
            "Create cli_blocked_write.txt now.",
        ),
    )
    text = event_text(events)
    require_contains(text, "PreToolUse:Write", "Write PreToolUse hook")
    if not any(payload.get("decision") == "block" for payload in hook_payloads(events, "PreToolUse")):
        raise AssertionError("Write PreToolUse hook did not return decision=block")
    require_hook_output_contains(events, "PreToolUse", "taskctl.py capability", "blocked guidance uses capability")
    if target.exists():
        target.unlink()
        raise AssertionError("Write block test failed: cli_blocked_write.txt was created")


def check_taskctl_dry_run_via_bash() -> None:
    command = f"{script_command('taskctl.py')} run-next 7 --dry-run"
    events = run_claude(
        "run-next-dry-run",
        base_claude_args(
            "--tools",
            "Bash",
            "--permission-mode",
            "bypassPermissions",
            "--max-budget-usd",
            "0.30",
            "--system-prompt",
            f"You are a CLI integration test harness. Use Bash exactly once to run: {command}. Then summarize whether the output mentions task_id or no ready task. Do not edit files.",
            "Run the taskctl dry-run command now.",
        ),
        timeout=360,
    )
    text = event_text(events)
    require_contains(text, "PreToolUse:Bash", "Bash PreToolUse hook")
    if not any(payload.get("continue") is True for payload in hook_payloads(events, "PreToolUse")):
        raise AssertionError("Bash PreToolUse hook did not return continue=true")
    require_contains(text, "task", "run-next dry-run task output")


def main() -> int:
    check_version()
    check_smoke()
    check_routing_context()
    check_write_block()
    check_taskctl_dry_run_via_bash()
    print("CLAUDE CLI FLOW CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
