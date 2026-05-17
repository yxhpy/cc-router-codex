#!/usr/bin/env python3
"""PreToolUse hook that routes writes through the control plane."""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import sys
from typing import Any

import command_catalog
import project_init
from claude_write_policy import classify_direct_write, maintenance_enable_hint
from hook_context import hook_tool_input, hook_tool_name, is_grok_hook, target_workspace
from llm_router import (
    call_codex_json,
    codex_model,
    codex_reasoning_effort,
    codex_timeout,
    load_project_env,
    provider_mode,
)
from project_paths import parse_env_file


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
    command_hint = command_catalog.inspect_command("capability", workspace)
    capability_hint = command_catalog.next_command("capability", workspace)
    return f"""Do not write production files directly from Claude.
Use exactly one atomic control-plane command for the target project workspace.
Required command: taskctl.py capability.

{capability_hint}

For exact local command contracts, run:
{command_hint}

Run the absolute Python command directly. Do not use cmd-only `cd /d`, and do
not change into the control-plane repository unless it is the user's actual
target project.

Allowed direct Claude writes are limited to runtime state under .claude/artifacts
and .claude/task-plans. Control-plane source/config writes need explicit
maintenance mode. Product code must be written by a Codex worker through
taskctl, normally using role fullstack; image-only assets should use assetgen,
which runs the built-in Codex raster backend at .claude/scripts/assetgen_exec.py.

For long capability prompts, use the file tool to write a UTF-8 prompt file
under the target workspace's .claude/task-plans/ directory, then run
taskctl.py capability with --prompt-file .claude/task-plans/<name>.txt instead
of --prompt. Do not use shell heredocs, redirection, tee, or /tmp prompt files.
"""


def allow(*, grok: bool = False) -> None:
    if grok:
        print(json.dumps({"decision": "allow", "continue": True}))
        return
    print(json.dumps({"continue": True}))


def block(reason: str, workspace: str, next_command_name: str = "capability", *, grok: bool = False) -> None:
    replacement_command = command_catalog.next_command(next_command_name, workspace)
    contract_command = command_catalog.inspect_command(next_command_name, workspace)
    message = f"{reason}\n\n{taskctl_guidance(workspace)}"
    payload = {
        "decision": "deny" if grok else "block",
        "reason": message,
        "continue": False,
        "next_command": contract_command,
        "replacement_command": replacement_command,
        "command_contract": contract_command,
        "systemMessage": message,
    }
    if not grok:
        payload["stopReason"] = message
        payload["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }
    print(json.dumps(payload, ensure_ascii=False))
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


def _is_python_executable_token(value: str) -> bool:
    token = str(value or "").strip().strip('"\'').replace("\\", "/")
    name = token.rsplit("/", 1)[-1]
    return re.fullmatch(PYTHON_EXE_NAME, name, re.IGNORECASE) is not None


def _extract_python_inline_code(cmd: str) -> str | None:
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        return None
    if len(tokens) < 3 or not _is_python_executable_token(tokens[0]):
        return None
    for index, token in enumerate(tokens[1:], 1):
        if token == "-c" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("-c") and len(token) > 2:
            return token[2:]
    return None


def _dotted_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _dotted_call_name(node.func)
    return ""


def _contains_mutating_python_text(code: str) -> bool:
    lower = str(code or "").lower()
    patterns = [
        r"\b(open|exec|eval|compile|__import__)\s*\(",
        r"\b(write|writelines|write_text|write_bytes|truncate|touch)\s*\(",
        r"\b(unlink|remove|rmdir|removedirs|mkdir|makedirs|rename|replace|chmod|chown)\s*\(",
        r"\b(os\.system|os\.popen|subprocess\.)",
        r"\bshutil\.(copy|copyfile|copytree|move|rmtree)\s*\(",
        r"\bsqlite3\b.*\b(update|insert|delete|replace|drop|alter|create)\b",
        r"\bexecute(?:many|script)?\s*\(\s*[\"']\s*(update|insert|delete|replace|drop|alter|create)\b",
    ]
    return any(re.search(pattern, lower, re.DOTALL) for pattern in patterns)


class _InlinePythonReadOnlyVisitor(ast.NodeVisitor):
    mutating_attrs = {
        "write",
        "writelines",
        "write_text",
        "write_bytes",
        "truncate",
        "touch",
        "unlink",
        "remove",
        "rmdir",
        "removedirs",
        "mkdir",
        "makedirs",
        "rename",
        "replace",
        "chmod",
        "chown",
        "copy",
        "copyfile",
        "copytree",
        "move",
        "rmtree",
    }
    unsafe_calls = {"open", "exec", "eval", "compile", "__import__", "input"}
    sql_mutation = re.compile(r"^\s*(update|insert|delete|replace|drop|alter|create)\b", re.IGNORECASE)

    def __init__(self) -> None:
        self.unsafe = False

    def visit_Call(self, node: ast.Call) -> Any:
        name = _dotted_call_name(node.func)
        attr = name.rsplit(".", 1)[-1] if name else ""
        if name in self.unsafe_calls or attr in self.unsafe_calls or attr in self.mutating_attrs:
            self.unsafe = True
            return
        if name in {"os.system", "os.popen"} or name.startswith("subprocess."):
            self.unsafe = True
            return
        if attr in {"execute", "executemany", "executescript"}:
            for arg in node.args[:1]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and self.sql_mutation.search(arg.value):
                    self.unsafe = True
                    return
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> Any:
        self.unsafe = True


def is_readonly_python_inline(cmd: str) -> bool:
    code = _extract_python_inline_code(cmd)
    if code is None or _contains_mutating_python_text(code):
        return False
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Let harmless typo/probe commands fail in the shell instead of being
        # mistaken for file writes by the hook.
        return True
    visitor = _InlinePythonReadOnlyVisitor()
    visitor.visit(tree)
    return not visitor.unsafe


def is_safe_bash_command(cmd: str) -> bool:
    return (
        is_safe_control_script(cmd)
        or is_readonly_python_inline(cmd)
        or any(re.search(pattern, cmd, re.IGNORECASE) for pattern in SAFE_BASH_CMDS)
    )


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


def mask_shell_quoted_text(cmd: str) -> str:
    """Mask shell-quoted spans so literal prompt text is not scanned as syntax."""
    chars = list(cmd)
    quote: str | None = None
    index = 0
    while index < len(chars):
        char = chars[index]
        if quote:
            if quote == '"' and char == "\\":
                chars[index] = " "
                if index + 1 < len(chars):
                    chars[index + 1] = " "
                    index += 2
                    continue
            if char == quote:
                quote = None
            else:
                chars[index] = " "
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "\\":
            chars[index] = " "
            if index + 1 < len(chars):
                chars[index + 1] = " "
                index += 2
                continue
        index += 1
    return "".join(chars)


def _redirection_target_is_null_device(cmd: str, start: int) -> tuple[bool, int]:
    index = start
    while index < len(cmd) and cmd[index].isspace():
        index += 1
    if index >= len(cmd):
        return False, index

    if cmd[index] in {"'", '"'}:
        quote = cmd[index]
        index += 1
        word_start = index
        while index < len(cmd):
            if quote == '"' and cmd[index] == "\\":
                index += 2
                continue
            if cmd[index] == quote:
                word = cmd[word_start:index]
                return word.replace("\\", "/").lower() in {"/dev/null", "nul", "nul:"}, index + 1
            index += 1
        return False, index

    word_start = index
    while index < len(cmd) and not cmd[index].isspace() and cmd[index] not in {";", "|", "&"}:
        index += 1
    word = cmd[word_start:index]
    return word.replace("\\", "/").lower() in {"/dev/null", "nul", "nul:"}, index


def has_shell_file_redirection(cmd: str) -> bool:
    quote: str | None = None
    index = 0
    while index < len(cmd):
        char = cmd[index]
        if quote:
            if quote == '"' and char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char == "&" and index + 1 < len(cmd) and cmd[index + 1] == ">":
            is_null, next_index = _redirection_target_is_null_device(cmd, index + 2)
            if is_null:
                index = next_index
                continue
            return True
        if char == ">":
            target = index + 1
            if target < len(cmd) and cmd[target] in {">", "|"}:
                target += 1
            if target < len(cmd) and cmd[target] == "&":
                fd_target = target + 1
                if fd_target < len(cmd) and (cmd[fd_target].isdigit() or cmd[fd_target] == "-"):
                    index = fd_target + 1
                    continue
            is_null, next_index = _redirection_target_is_null_device(cmd, target)
            if is_null:
                index = next_index
                continue
            return True
        index += 1
    return False


def bash_block_reason(bash_cmd: str, workspace: str) -> str | None:
    cmd = bash_cmd.strip()

    if re.search(r"^(codex|codex\.cmd)\s+exec\b", cmd, re.IGNORECASE):
        return f"Bash file operation blocked: {cmd[:200]}"

    safe_command = is_safe_bash_command(cmd)

    if has_shell_file_redirection(cmd):
        return f"Bash file operation blocked: {cmd[:200]}"

    shell_syntax_scan = mask_shell_quoted_text(cmd)
    shell_mutating_patterns = [
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
        r"\b(Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item|Rename-Item)\b",
        r"\bapply_patch\b",
    ]
    if any(re.search(pattern, shell_syntax_scan, re.IGNORECASE | re.DOTALL) for pattern in shell_mutating_patterns):
        return f"Bash file operation blocked: {cmd[:200]}"

    executable_inline_mutating_patterns = [
        r"python.*-c.*open\(",
        r"python.*-c.*\bsqlite3\b.*\b(update|insert|delete|replace|drop|alter|create)\b",
        r"python.*-c.*\bconn\.execute\s*\(\s*[\"']\s*(update|insert|delete|replace|drop|alter|create)\b",
        r"\b\[?(System\.)?IO\.File\]::(WriteAllText|WriteAllLines|AppendAllText|AppendAllLines)\b",
        r"\b(?:powershell|pwsh)(?:\.exe)?\b.*\b(Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item|Rename-Item)\b",
    ]
    if not safe_command and any(re.search(pattern, cmd, re.IGNORECASE | re.DOTALL) for pattern in executable_inline_mutating_patterns):
        return f"Bash file operation blocked: {cmd[:200]}"

    if safe_command:
        return None

    allowed, reason = review_ambiguous_bash_command(cmd, workspace)
    if allowed:
        return None
    return f"Bash command rejected by gpt-5.4-mini guard: {reason}. Command: {cmd[:200]}"


def has_file_creation(bash_cmd: str) -> bool:
    return bash_block_reason(bash_cmd, str(os.getcwd())) is not None


def handle_write(tool_name: str, tool_input: dict[str, object], workspace: str, *, grok: bool = False) -> None:
    file_path = str(tool_input.get("file_path") or tool_input.get("filePath") or tool_input.get("path") or "")
    decision = classify_direct_write(file_path, workspace=workspace)
    if decision.allowed:
        allow(grok=grok)
        return

    extra = ""
    if decision.category == "control-plane":
        extra = "\n" + maintenance_enable_hint()
    block(
        f"{tool_name} blocked for {decision.category} path {decision.relative_path}: {decision.reason}{extra}",
        workspace,
        grok=grok,
    )


def main() -> None:
    try:
        hook_input = read_hook_json()
    except Exception:
        print(json.dumps({"continue": True}))
        return

    grok = is_grok_hook(hook_input)
    tool_name = hook_tool_name(hook_input)
    tool_input = hook_tool_input(hook_input)
    workspace = target_workspace(hook_input)
    project_init.apply_project_environment(workspace)

    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        handle_write(tool_name, tool_input, workspace, grok=grok)
        return

    if tool_name == "Task":
        block("Task/Subagent blocked: use taskctl capability instead", workspace, grok=grok)

    if tool_name == "Bash":
        bash_cmd = str(tool_input.get("command", ""))
        reason = bash_block_reason(bash_cmd, workspace)
        if reason:
            block(reason, workspace, grok=grok)
        allow(grok=grok)
        return

    allow(grok=grok)


if __name__ == "__main__":
    main()
