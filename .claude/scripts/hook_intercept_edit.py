#!/usr/bin/env python3
"""Compatibility hook for Write/Edit tools.

settings.json uses hook_intercept_create.py for all PreToolUse checks. This
file remains as a narrow compatibility entrypoint and delegates to the same
direct-write policy.
"""

from __future__ import annotations

import json
import sys

from claude_write_policy import classify_direct_write, maintenance_enable_hint
from hook_context import target_workspace
from project_paths import script_path


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"continue": True}))
        return

    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input", {})
    if tool_name not in ("Write", "Edit", "MultiEdit", "NotebookEdit") or not isinstance(tool_input, dict):
        print(json.dumps({"continue": True}))
        return

    decision = classify_direct_write(str(tool_input.get("file_path", "")))
    if decision.allowed:
        print(json.dumps({"continue": True}))
        return

    workspace = target_workspace(payload)
    reason = (
        f"{tool_name} blocked for {decision.category} path {decision.relative_path}: {decision.reason}\n\n"
        "Required command: taskctl.py capability.\n"
        f'Use: python "{script_path("taskctl.py")}" capability --role <role> --title "<title>" '
        f'--prompt "<bounded worker prompt>" --artifact <kind:path> --workspace "{workspace}" --goal "<user goal>"\n'
        "Run the absolute Python command directly; do not use cmd-only `cd /d`."
    )
    if decision.category == "control-plane":
        reason += "\n" + maintenance_enable_hint()

    print(json.dumps({
        "continue": False,
        "stopReason": reason,
        "systemMessage": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
