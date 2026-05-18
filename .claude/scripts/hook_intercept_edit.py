#!/usr/bin/env python3
"""Compatibility hook for Write/Edit tools.

settings.json uses hook_intercept_create.py for all PreToolUse checks. This
file remains as a narrow compatibility entrypoint and delegates to the same
direct-write policy.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claude_write_policy import classify_direct_write, maintenance_enable_hint
from hook_context import hook_tool_input, hook_tool_name, is_grok_hook, target_workspace
import project_init
from project_paths import script_path


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"continue": True}))
        return

    grok = is_grok_hook(payload)
    tool_name = hook_tool_name(payload)
    tool_input = hook_tool_input(payload)
    if tool_name not in ("Write", "Edit", "MultiEdit", "NotebookEdit") or not isinstance(tool_input, dict):
        print(json.dumps({"decision": "allow", "continue": True} if grok else {"continue": True}))
        return

    workspace = target_workspace(payload)
    project_init.apply_project_environment(workspace)

    file_path = str(tool_input.get("file_path") or tool_input.get("filePath") or tool_input.get("path") or "")
    decision = classify_direct_write(file_path)
    if decision.allowed:
        print(json.dumps({"decision": "allow", "continue": True} if grok else {"continue": True}))
        return

    reason = (
        f"{tool_name} blocked for {decision.category} path {decision.relative_path}: {decision.reason}\n\n"
        "Required command: taskctl.py capability.\n"
        f'Use: python "{script_path("taskctl.py")}" capability --role <role> --title "<title>" '
        f'--prompt "<bounded worker prompt>" --artifact <kind:path> --workspace "{workspace}" --goal "<user goal>"\n'
        "Run the absolute Python command directly; do not use cmd-only `cd /d`."
    )
    if decision.category == "control-plane":
        reason += "\n" + maintenance_enable_hint()

    output = {
        "decision": "deny" if grok else "block",
        "continue": False,
        "reason": reason,
        "systemMessage": reason,
    }
    if not grok:
        output["stopReason"] = reason
        output["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    print(json.dumps(output, ensure_ascii=False))
    raise SystemExit(2)


if __name__ == "__main__":
    main()
