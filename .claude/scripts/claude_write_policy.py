#!/usr/bin/env python3
"""Direct-write policy for Claude Code controller hooks.

The controller may keep small runtime state under .claude, but control-plane
source/config changes require an explicit maintenance switch. Product files are
always routed through taskctl/Codex workers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from project_paths import REPO_ROOT, repo_relative, resolve_in_repo


CONTROL_PLANE_WRITE_ENV = "CLAUDE_CONTROL_PLANE_WRITE"
CONTROL_PLANE_WRITE_MARKER = ".claude/ALLOW_CONTROL_PLANE_WRITES"

RUNTIME_WRITE_PREFIXES = (
    ".claude/artifacts/",
    ".claude/task-plans/",
)

RUNTIME_WRITE_FILES = (
    ".claude/scheduled_tasks.json",
)

CONTROL_PLANE_PREFIXES = (
    ".claude/scripts/",
    ".claude/skills/",
    ".claude/plugins/",
)

CONTROL_PLANE_FILES = (
    "CLAUDE.md",
    ".claude/MANDATORY_CONTEXT.md",
    ".claude/CONTROL_PLANE_POLICY.md",
    ".claude/WRITE_POLICY.md",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/model_policy.json",
    ".claude/.env.example",
    ".claude/.gitignore",
)

SYNC_MANAGED_PREFIXES = (
    ".claude/design-references/",
)


@dataclass(frozen=True)
class WriteDecision:
    allowed: bool
    category: str
    reason: str
    relative_path: str


def _normalized_relative(file_path: str) -> str:
    if not file_path:
        return ""
    return repo_relative(resolve_in_repo(file_path)).replace("\\", "/")


def _has_prefix(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value == prefix.rstrip("/") or value.startswith(prefix) for prefix in prefixes)


def _parse_expiry(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            text = str(parsed.get("expires_at") or parsed.get("expires_at_utc") or "")
        elif isinstance(parsed, str):
            text = parsed
    except json.JSONDecodeError:
        pass
    text = text.strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def marker_expiry() -> datetime | None:
    path = REPO_ROOT / CONTROL_PLANE_WRITE_MARKER
    if not path.exists():
        return None
    try:
        return _parse_expiry(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def marker_state() -> str:
    path = REPO_ROOT / CONTROL_PLANE_WRITE_MARKER
    if not path.exists():
        return "missing"
    expires_at = marker_expiry()
    if expires_at is None:
        return "invalid"
    if expires_at <= datetime.now(timezone.utc):
        return "expired"
    return "active"


def control_plane_write_enabled(env: dict[str, str] | None = None) -> bool:
    values = env if env is not None else os.environ
    if str(values.get(CONTROL_PLANE_WRITE_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}:
        return True
    expires_at = marker_expiry()
    return bool(expires_at and expires_at > datetime.now(timezone.utc))


def classify_direct_write(file_path: str, env: dict[str, str] | None = None) -> WriteDecision:
    rel = _normalized_relative(file_path)
    if not rel:
        return WriteDecision(False, "unknown", "empty path", rel)

    if rel in RUNTIME_WRITE_FILES or _has_prefix(rel, RUNTIME_WRITE_PREFIXES):
        return WriteDecision(True, "runtime", "allowed runtime state under .claude", rel)

    if rel in CONTROL_PLANE_FILES or _has_prefix(rel, CONTROL_PLANE_PREFIXES):
        if control_plane_write_enabled(env):
            return WriteDecision(True, "control-plane", "explicit control-plane maintenance write enabled", rel)
        return WriteDecision(
            False,
            "control-plane",
            "control-plane source/config writes require explicit maintenance mode",
            rel,
        )

    if _has_prefix(rel, SYNC_MANAGED_PREFIXES):
        return WriteDecision(
            False,
            "managed-resource",
            "design references are sync-managed; use sync_design_refs.py instead of direct writes",
            rel,
        )

    return WriteDecision(False, "product", "product or external path must be written by Codex through taskctl", rel)


def maintenance_enable_hint() -> str:
    marker = Path(CONTROL_PLANE_WRITE_MARKER).as_posix()
    return (
        f"Enable only for an explicit control-plane maintenance session by setting "
        f"{CONTROL_PLANE_WRITE_ENV}=1 before launching Claude, or by creating {marker} "
        "outside the controller flow with JSON like "
        '{"expires_at":"2026-05-14T15:30:00Z"} and deleting it after maintenance.'
    )
