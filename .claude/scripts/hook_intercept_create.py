#!/usr/bin/env python3
"""PreToolUse hook that routes writes through the control plane."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from claude_write_policy import classify_direct_write, maintenance_enable_hint
from hook_context import target_workspace
from llm_router import (
    call_codex_json,
    codex_model,
    codex_reasoning_effort,
    codex_timeout,
    load_project_env,
    provider_mode,
)
from project_paths import parse_env_file, python_command, script_path


SAFE_SCRIPT_NAMES = (
    "taskctl",
    "codex_exec",
    "codex_log",
    "safety_filter",
    "task_input_filter",
    "sync_design_refs",
    "prompt_template_mcp",
    "focus_guard",
    "mcp_inventory",
    "hook_",
)

PYTHON_EXE_NAME = r"(?:python(?:\d+(?:\.\d+)*)?|py)(?:\.exe)?"
PYTHON_CMD = (
    r'(?:"[^"]*[\\/]' + PYTHON_EXE_NAME + r'"|"'
    + PYTHON_EXE_NAME
    + r'"|\'[^\']*[\\/]'
    + PYTHON_EXE_NAME
    + r"\'|\'"
    + PYTHON_EXE_NAME
    + r"\'|\S*[\\/]"
    + PYTHON_EXE_NAME
    + r"|"
    + PYTHON_EXE_NAME
    + r")"
)

SAFE_BASH_CMDS = [
    # Control-plane tooling. File writes inside these scripts are policy-owned.
    r"^" + PYTHON_CMD + r'\s+["\']?\.claude[\\/]scripts[\\/](?:'
    + "|".join(SAFE_SCRIPT_NAMES)
    + r')\S*\.py["\']?',
    r"^" + PYTHON_CMD + r'\s+(?:"[^"]*[\\/]\.claude[\\/]scripts[\\/](?:'
    + "|".join(SAFE_SCRIPT_NAMES)
    + r')\S*\.py"|\'[^\']*[\\/]\.claude[\\/]scripts[\\/](?:'
    + "|".join(SAFE_SCRIPT_NAMES)
    + r')\S*\.py\'|\S*[\\/]\.claude[\\/]scripts[\\/](?:'
    + "|".join(SAFE_SCRIPT_NAMES)
    + r')\S*\.py|\S*scripts[\\/](?:'
    + "|".join(SAFE_SCRIPT_NAMES)
    + r')\S*\.py)',
    # Git inspection only.
    r"^git\s+(status|log|diff|branch|stash|show|whatchanged|blame|grep)\b",
    # Read-only inspection.
    r"^(ls|dir|pwd|cd|which|where|type|whoami|hostname|date|env|printenv)\b",
    r"^(wc|head|tail|sort|uniq|cut|tr)\b",
    r"^(cat|less|more)\s",
    r"^(echo|printf)\s",
    r"^(find|locate|file|stat)\b",
    r"^(uname|ps|top|df|du|free)\b",
    r"^(curl|wget)\s+\S+\s*$",
    r"^(tar|zip|unzip|gzip|gunzip)\s.*-[tvl].*",
    # Project lifecycle commands. These may update dependency caches or build
    # outputs, but they are not direct Claude-authored product-file writes.
    r"^(npm|pnpm|yarn|bun)\s+(install|i|ci|add|remove|uninstall|run|test|start|exec|dlx|create|build|dev)\b",
    r"^grep\b",
    r"^rg\b",
]

BASH_GUARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "allow": {"type": "boolean"},
        "direct_file_write": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 500},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["allow", "direct_file_write", "reason", "confidence"],
    "additionalProperties": False,
}

BASH_GUARD_PROMPT = """You are a Bash command safety reviewer for a Claude/Codex control plane.

Return exactly one JSON object matching the schema: allow, direct_file_write, reason, confidence.

Policy:
- Block commands that directly create, edit, append, overwrite, rename, copy, move, or delete project files from the shell.
- Block output redirection to files, tee-to-file, heredoc-to-file, inline scripts that call file write APIs, download-to-file commands, destructive filesystem commands, direct apply_patch, and direct codex exec bypassing taskctl.
- Allow read-only inspection, package-manager lifecycle commands, test/build/dev-server commands, version-control metadata commands, and control-plane scripts when they do not explicitly write file contents from the shell command.
- Package managers may update dependency/cache/build artifacts, but they are not Claude-authored direct file writes; allow them unless the command also contains explicit shell file-writing operators.
- If uncertain, set allow=false.

Classify only the provided JSON payload's command. Do not run commands or suggest alternatives.
"""


def taskctl_guidance(workspace: str) -> str:
    return f"""Do not write production files directly from Claude.
Use exactly one atomic control-plane command for the target project workspace.
Required command: taskctl.py capability.

{python_command()} "{script_path('taskctl.py')}" capability --role <role> --title "<title>" --prompt "<bounded worker prompt>" --artifact <kind:path> --workspace "{workspace}" --goal "<user goal>"

Run the absolute Python command directly. Do not use cmd-only `cd /d`, and do
not change into the control-plane repository unless it is the user's actual
target project.

Allowed direct Claude writes are limited to runtime state under .claude/artifacts
and .claude/task-plans. Control-plane source/config writes need explicit
maintenance mode. Product code must be written by a Codex worker through
taskctl, normally using role fullstack; image-only assets should use assetgen,
which runs the built-in Codex raster backend at .claude/scripts/assetgen_exec.py.
"""


def block(reason: str, workspace: str) -> None:
    message = f"{reason}\n\n{taskctl_guidance(workspace)}"
    print(json.dumps({
        "decision": "block",
        "reason": message,
        "continue": False,
        "stopReason": message,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        },
        "systemMessage": message,
    }, ensure_ascii=False))
    raise SystemExit(2)


def read_hook_json() -> dict[str, object]:
    raw = sys.stdin.buffer.read()
    if not raw:
        return {}
    for encoding in ("utf-8", "gb18030"):
        try:
            parsed = json.loads(raw.decode(encoding))
            if isinstance(parsed, dict):
                return parsed
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return {}


def is_safe_control_script(cmd: str) -> bool:
    match = re.match(
        r"^(?:py|python|python3)\s+(?:\"(?P<double>[^\"]+)\"|'(?P<single>[^']+)'|(?P<bare>\S+))",
        cmd.strip(),
        re.IGNORECASE,
    )
    if not match:
        return False
    script = (match.group("double") or match.group("single") or match.group("bare") or "").replace("\\", "/")
    name = script.rsplit("/", 1)[-1]
    if not name.endswith(".py"):
        return False
    if not any(name.startswith(prefix) for prefix in SAFE_SCRIPT_NAMES):
        return False
    return script.startswith(".claude/scripts/") or "/.claude/scripts/" in f"/{script}"


def is_safe_bash_command(cmd: str) -> bool:
    return is_safe_control_script(cmd) or any(re.search(pattern, cmd, re.IGNORECASE) for pattern in SAFE_BASH_CMDS)


def env_flag(name: str, default: str = "") -> bool:
    value = os.environ.get(name, default)
    return str(value).strip().lower() not in {"", "0", "false", "off", "none", "no"}


def load_bash_guard_env() -> None:
    try:
        load_project_env()
        return
    except Exception:
        pass
    for key, value in parse_env_file().items():
        os.environ.setdefault(key, value)


def redact_command_for_review(cmd: str) -> str:
    redacted = str(cmd or "")
    redacted = re.sub(
        r"(?i)\b((?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASS|API_KEY|ACCESS_KEY)[A-Z0-9_]*)=)([^\s]+)",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(\bsshpass\s+-p\s+)(?:\"[^\"]*\"|'[^']*'|\S+)",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(\b--?(?:password|passwd|token|secret|api-key|access-key|key)\s+)(?:\"[^\"]*\"|'[^']*'|\S+)",
        r"\1[REDACTED]",
        redacted,
    )
    return redacted


def mock_bash_guard_decision() -> tuple[bool, str] | None:
    raw = os.environ.get("TASKCTL_BASH_GUARD_MOCK_JSON")
    if raw is None or raw == "":
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"invalid TASKCTL_BASH_GUARD_MOCK_JSON: {exc}"
    if not isinstance(payload, dict):
        return False, "TASKCTL_BASH_GUARD_MOCK_JSON must be a JSON object"
    allowed = bool(payload.get("allow")) and not bool(payload.get("direct_file_write"))
    reason = str(payload.get("reason") or "mocked Bash guard decision")
    return allowed, reason


def review_ambiguous_bash_command(cmd: str, workspace: str) -> tuple[bool, str]:
    load_bash_guard_env()
    mocked = mock_bash_guard_decision()
    if mocked is not None:
        return mocked

    if not env_flag("TASKCTL_BASH_GUARD", "off"):
        return False, "Bash command is not in the deterministic allowlist and TASKCTL_BASH_GUARD is disabled"

    mode = provider_mode("bash_guard")
    if mode != "codex":
        return False, f"Bash guard provider {mode!r} is unsupported; use TASKCTL_BASH_GUARD_PROVIDER=codex"

    timeout = int(os.environ.get("TASKCTL_BASH_GUARD_TIMEOUT", "25"))
    try:
        payload = call_codex_json(
            system_prompt=BASH_GUARD_PROMPT,
            user_content=json.dumps(
                {
                    "command": redact_command_for_review(cmd),
                    "workspace": workspace,
                },
                ensure_ascii=False,
            ),
            schema=BASH_GUARD_SCHEMA,
            model=codex_model("bash_guard"),
            reasoning_effort=codex_reasoning_effort("bash_guard"),
            timeout=codex_timeout("bash_guard", timeout),
        )
    except Exception as exc:
        return False, f"gpt-5.4-mini Bash guard failed: {exc}"

    allowed = bool(payload.get("allow")) and not bool(payload.get("direct_file_write"))
    reason = str(payload.get("reason") or "gpt-5.4-mini Bash guard decision")
    return allowed, reason


def without_fd_redirects(cmd: str) -> str:
    return re.sub(r"[0-9]?>&[0-9]+", "", cmd)


def bash_block_reason(bash_cmd: str, workspace: str) -> str | None:
    cmd = bash_cmd.strip()

    if re.search(r"^(codex|codex\.cmd)\s+exec\b", cmd, re.IGNORECASE):
        return f"Bash file operation blocked: {cmd[:200]}"

    safe_command = is_safe_bash_command(cmd)
    redirection_scan = without_fd_redirects(cmd)

    if re.search(r"(^|[^&])(?:[0-9])?>{1,2}\s*\S", redirection_scan):
        return f"Bash file operation blocked: {cmd[:200]}"
    if re.search(r"&>{1,2}\s*\S", redirection_scan):
        return f"Bash file operation blocked: {cmd[:200]}"
    if re.search(r"<<.*\S.*(?:>|>>)\s*\S", redirection_scan):
        return f"Bash file operation blocked: {cmd[:200]}"

    mutating_patterns = [
        r"\btee\b",
        r"\btouch\b",
        r"\bcp\b|\bcopy\b",
        r"\bmv\b|\bmove\b|\bren\b|\brename\b",
        r"\brm\b|\bdel\b",
        r"\bmkdir\b|\brmdir\b",
        r"\bdd\b",
        r"\bsed\b.*-i",
        r"(curl|wget).*(-o|--output)\b",
        r"\b(Invoke-WebRequest|iwr|Invoke-RestMethod|irm)\b.*\b(-OutFile|-o)\b",
        r"\bStart-BitsTransfer\b.*\b-Destination\b",
        r"\bExport-(Csv|Clixml|Console|PSSession)\b",
        r"\bSet-Item(Property)?\b|\bClear-Item(Property)?\b",
        r"python.*-c.*open\(",
        r"python.*-c.*\bsqlite3\b.*\b(update|insert|delete|replace|drop|alter|create)\b",
        r"python.*-c.*\bconn\.execute\s*\(\s*[\"']\s*(update|insert|delete|replace|drop|alter|create)\b",
        r"\b(Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item|Rename-Item)\b",
        r"\b\[?(System\.)?IO\.File\]::(WriteAllText|WriteAllLines|AppendAllText|AppendAllLines)\b",
        r"\bapply_patch\b",
    ]
    if any(re.search(pattern, cmd, re.IGNORECASE | re.DOTALL) for pattern in mutating_patterns):
        return f"Bash file operation blocked: {cmd[:200]}"

    if safe_command:
        return None

    allowed, reason = review_ambiguous_bash_command(cmd, workspace)
    if allowed:
        return None
    return f"Bash command rejected by gpt-5.4-mini guard: {reason}. Command: {cmd[:200]}"


def has_file_creation(bash_cmd: str) -> bool:
    return bash_block_reason(bash_cmd, str(os.getcwd())) is not None


def handle_write(tool_name: str, tool_input: dict[str, object], workspace: str) -> None:
    file_path = str(tool_input.get("file_path", ""))
    decision = classify_direct_write(file_path)
    if decision.allowed:
        print(json.dumps({"continue": True}))
        return

    extra = ""
    if decision.category == "control-plane":
        extra = "\n" + maintenance_enable_hint()
    block(f"{tool_name} blocked for {decision.category} path {decision.relative_path}: {decision.reason}{extra}", workspace)


def main() -> None:
    try:
        hook_input = read_hook_json()
    except Exception:
        print(json.dumps({"continue": True}))
        return

    tool_name = str(hook_input.get("tool_name", ""))
    raw_tool_input = hook_input.get("tool_input", {})
    tool_input = raw_tool_input if isinstance(raw_tool_input, dict) else {}
    workspace = target_workspace(hook_input)

    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        handle_write(tool_name, tool_input, workspace)
        return

    if tool_name == "Task":
        block("Task/Subagent blocked: use taskctl capability instead", workspace)

    if tool_name == "Bash":
        bash_cmd = str(tool_input.get("command", ""))
        reason = bash_block_reason(bash_cmd, workspace)
        if reason:
            block(reason, workspace)
        print(json.dumps({"continue": True}))
        return

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
