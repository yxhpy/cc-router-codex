#!/usr/bin/env python3
"""Hard focus-state guard for Claude controller sessions.

Production goals are active until the controller records either completion or
exhaustion. The Stop hook reads this state and blocks premature final answers.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from project_paths import script_command


STATE_RELATIVE = Path(".claude") / "task-plans" / "focus_state.json"
COMPLETED_STATUSES = {"complete", "exhausted"}


@dataclass(frozen=True)
class StopDecision:
    allow: bool
    reason: str
    state: Mapping[str, Any] | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolve_workspace(value: str | Path | None) -> Path:
    return Path(value or ".").expanduser().resolve()


def state_path(workspace: str | Path | None) -> Path:
    return resolve_workspace(workspace) / STATE_RELATIVE


def read_state(workspace: str | Path | None) -> dict[str, Any]:
    path = state_path(workspace)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {
            "schemaVersion": 1,
            "status": "active",
            "workspace": str(resolve_workspace(workspace)),
            "goal": "",
            "error": "focus state is invalid JSON",
        }
    return payload if isinstance(payload, dict) else {}


def write_state(workspace: str | Path | None, payload: Mapping[str, Any]) -> Path:
    path = state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def start_focus(
    *,
    workspace: str | Path,
    goal: str,
    role: str,
    title: str,
    artifacts: Sequence[str] | None = None,
    route_token: str = "",
    source: str = "",
) -> Path:
    now = utc_now()
    previous = read_state(workspace)
    payload = {
        "schemaVersion": 1,
        "status": "active",
        "workspace": str(resolve_workspace(workspace)),
        "goal": goal,
        "route": {
            "role": role,
            "title": title,
            "artifacts": list(artifacts or []),
            "route_token": route_token,
            "source": source,
        },
        "attempts": previous.get("attempts", []) if isinstance(previous.get("attempts"), list) else [],
        "startedAt": previous.get("startedAt") or now,
        "updatedAt": now,
        "completionEvidence": "",
        "exhaustionEvidence": "",
    }
    return write_state(workspace, payload)


def record_attempt(*, workspace: str | Path, summary: str, kind: str = "attempt") -> Path:
    payload = read_state(workspace) or {
        "schemaVersion": 1,
        "status": "active",
        "workspace": str(resolve_workspace(workspace)),
        "goal": "",
        "route": {},
        "attempts": [],
        "startedAt": utc_now(),
    }
    attempts = payload.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempts.append({"at": utc_now(), "kind": kind, "summary": summary})
    payload["attempts"] = attempts
    payload["status"] = "active"
    payload["updatedAt"] = utc_now()
    return write_state(workspace, payload)


def finish_focus(*, workspace: str | Path, status: str, evidence: str) -> Path:
    if status not in COMPLETED_STATUSES:
        raise SystemExit(f"ERROR: unsupported focus status: {status}")
    payload = read_state(workspace)
    if not payload:
        payload = {
            "schemaVersion": 1,
            "workspace": str(resolve_workspace(workspace)),
            "goal": "",
            "route": {},
            "attempts": [],
            "startedAt": utc_now(),
        }
    payload["status"] = status
    payload["updatedAt"] = utc_now()
    if status == "complete":
        payload["completionEvidence"] = evidence
    else:
        payload["exhaustionEvidence"] = evidence
    return write_state(workspace, payload)


def clear_focus(workspace: str | Path) -> bool:
    path = state_path(workspace)
    if path.exists():
        path.unlink()
        return True
    return False


def stop_decision(workspace: str | Path) -> StopDecision:
    payload = read_state(workspace)
    if not payload:
        return StopDecision(True, "no active focus state")
    status = str(payload.get("status") or "active").strip().lower()
    if status in COMPLETED_STATUSES:
        return StopDecision(True, f"focus state is {status}", payload)
    goal = str(payload.get("goal") or "").strip() or "<unknown goal>"
    attempts = payload.get("attempts") if isinstance(payload.get("attempts"), list) else []
    reason = (
        "FOCUS_GUARD_BLOCK: active production goal is not complete or exhausted.\n"
        f"Goal: {goal}\n"
        f"Recorded attempts: {len(attempts)}\n"
        "Continue executing the recommended taskctl capability path. If a method fails, "
        "record the attempt, inspect logs/artifacts, search or try another viable route, "
        "and continue.\n"
        "Only after reaching the requested result may you run:\n"
        f'{script_command("focus_guard.py")} complete --workspace "{resolve_workspace(workspace)}" --evidence "<artifacts/tests/result>"\n'
        "Only if all viable approaches have been exhausted may you run:\n"
        f'{script_command("focus_guard.py")} exhausted --workspace "{resolve_workspace(workspace)}" --evidence "<attempts and blockers>"'
    )
    return StopDecision(False, reason, payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage hard focus state for Claude/Codex task execution.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start or refresh active focus state.")
    start.add_argument("--workspace", default=".")
    start.add_argument("--goal", required=True)
    start.add_argument("--role", default="")
    start.add_argument("--title", default="")
    start.add_argument("--artifact", action="append")
    start.add_argument("--route-token", default="")
    start.add_argument("--source", default="")

    attempt = sub.add_parser("attempt", help="Record a failed or partial attempt.")
    attempt.add_argument("--workspace", default=".")
    attempt.add_argument("--summary", required=True)
    attempt.add_argument("--kind", default="attempt")

    complete = sub.add_parser("complete", help="Allow stopping because the goal has been reached.")
    complete.add_argument("--workspace", default=".")
    complete.add_argument("--evidence", required=True)

    exhausted = sub.add_parser("exhausted", help="Allow stopping because all viable routes were exhausted.")
    exhausted.add_argument("--workspace", default=".")
    exhausted.add_argument("--evidence", required=True)

    status = sub.add_parser("status", help="Print focus state.")
    status.add_argument("--workspace", default=".")
    status.add_argument("--json", action="store_true")

    clear = sub.add_parser("clear", help="Clear focus state.")
    clear.add_argument("--workspace", default=".")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "start":
        path = start_focus(
            workspace=args.workspace,
            goal=args.goal,
            role=args.role,
            title=args.title,
            artifacts=args.artifact,
            route_token=args.route_token,
            source=args.source,
        )
        print(f"FOCUS active: {path}")
        return 0
    if args.command == "attempt":
        path = record_attempt(workspace=args.workspace, summary=args.summary, kind=args.kind)
        print(f"FOCUS attempt recorded: {path}")
        return 0
    if args.command == "complete":
        path = finish_focus(workspace=args.workspace, status="complete", evidence=args.evidence)
        print(f"FOCUS complete: {path}")
        return 0
    if args.command == "exhausted":
        path = finish_focus(workspace=args.workspace, status="exhausted", evidence=args.evidence)
        print(f"FOCUS exhausted: {path}")
        return 0
    if args.command == "status":
        payload = read_state(args.workspace)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"FOCUS {payload.get('status', 'none') if payload else 'none'}")
        return 0
    if args.command == "clear":
        print("FOCUS cleared" if clear_focus(args.workspace) else "FOCUS none")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
