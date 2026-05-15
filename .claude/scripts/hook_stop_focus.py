#!/usr/bin/env python3
"""Stop hook that blocks premature final answers for active production goals."""

from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import focus_guard
from hook_context import target_workspace


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


def main() -> int:
    payload = read_hook_json()
    if payload.get("stop_hook_active") is True:
        print(json.dumps({"continue": True}))
        return 0

    workspace = target_workspace(payload)
    decision = focus_guard.stop_decision(workspace)
    if decision.allow:
        print(json.dumps({"continue": True}))
        return 0

    print(
        json.dumps(
            {
                "decision": "block",
                "continue": False,
                "stopReason": decision.reason,
                "reason": decision.reason,
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": decision.reason,
                },
                "systemMessage": decision.reason,
            },
            ensure_ascii=False,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
