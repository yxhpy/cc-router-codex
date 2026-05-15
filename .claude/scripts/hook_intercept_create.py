#!/usr/bin/env python3
"""PreToolUse hook that routes writes through the control plane."""

from __future__ import annotations

import json
import re
import sys

from claude_write_policy import classify_direct_write, maintenance_enable_hint
from hook_context import target_workspace
from project_paths import python_command, script_path


SAFE_SCRIPT_NAMES = (
    "taskctl",
    "codex_exec",
    "codex_log",
    "safety_filter",
    "task_input_filter",
    "sync_design_refs",
    "mcp_inventory",
    "hook_",
)

PYTHON_CMD = r'(?:"[^"]*(?:python|python3|py)(?:\.exe)?"|\S*(?:python|python3|py)(?:\.exe)?|py|python|python3)'

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
    r"^grep\b",
    r"^rg\b",
]

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


def without_fd_redirects(cmd: str) -> str:
    return re.sub(r"[0-9]?>&[0-9]+", "", cmd)


def has_file_creation(bash_cmd: str) -> bool:
    cmd = bash_cmd.strip()

    if re.search(r"^(codex|codex\.cmd)\s+exec\b", cmd, re.IGNORECASE):
        return True

    safe_command = is_safe_bash_command(cmd)
    redirection_scan = without_fd_redirects(cmd)

    if re.search(r"(^|[^&])(?:[0-9])?>{1,2}\s*\S", redirection_scan):
        return True
    if re.search(r"&>{1,2}\s*\S", redirection_scan):
        return True
    if re.search(r"<<.*\S.*(?:>|>>)\s*\S", redirection_scan):
        return True

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
        return True

    if safe_command:
        return False

    return True


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
        if has_file_creation(bash_cmd):
            block(f"Bash file operation blocked: {bash_cmd[:200]}", workspace)
        print(json.dumps({"continue": True}))
        return

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
