#!/usr/bin/env python3
"""Short-lived route tokens for LLM-routed taskctl capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Any, Iterable

from project_paths import REPO_ROOT


DEFAULT_CACHE_PATH = REPO_ROOT / ".claude" / "task-plans" / "route-cache.json"
DEFAULT_TTL_SECONDS = 15 * 60
MAX_CACHE_ENTRIES = 100


@dataclass(frozen=True)
class RouteTokenCheck:
    accepted: bool
    reason: str


def _cache_path() -> Path:
    return Path(os.environ.get("TASKCTL_ROUTE_CACHE_PATH", str(DEFAULT_CACHE_PATH))).expanduser().resolve()


def _ttl_seconds() -> int:
    try:
        return max(30, int(os.environ.get("TASKCTL_ROUTE_TOKEN_TTL_SECONDS", str(DEFAULT_TTL_SECONDS))))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _normalize_workspace(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(Path(value).expanduser().resolve(strict=False))
    except OSError:
        return str(value)


def _normalize_artifacts(values: Iterable[Any] | None) -> list[str]:
    return [str(item).strip() for item in (values or []) if str(item).strip()]


def route_fingerprint(
    *,
    role: str,
    title: str,
    prompt: str,
    artifacts: Iterable[Any] | None,
    workspace: str | None,
    goal: str | None,
) -> str:
    payload = {
        "role": str(role or "").strip(),
        "title": str(title or "").strip(),
        "prompt": str(prompt or "").strip(),
        "artifacts": _normalize_artifacts(artifacts),
        "workspace": _normalize_workspace(workspace),
        "goal": str(goal or "").strip(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"tokens": {}}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {"tokens": {}}
    if not isinstance(loaded, dict):
        return {"tokens": {}}
    tokens = loaded.get("tokens")
    if not isinstance(tokens, dict):
        loaded["tokens"] = {}
    return loaded


def _save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _pruned_tokens(tokens: dict[str, Any], now_ts: int) -> dict[str, Any]:
    live: list[tuple[str, dict[str, Any]]] = []
    for token, entry in tokens.items():
        if not isinstance(entry, dict):
            continue
        try:
            expires_at = int(entry.get("expires_at", 0))
        except (TypeError, ValueError):
            continue
        if expires_at <= now_ts:
            continue
        live.append((str(token), entry))
    live.sort(key=lambda item: int(item[1].get("created_at", 0)), reverse=True)
    return dict(live[:MAX_CACHE_ENTRIES])


def store_route_token(
    *,
    role: str,
    title: str,
    prompt: str,
    artifacts: Iterable[Any] | None,
    workspace: str | None,
    goal: str | None,
    source: str,
    confidence: float,
) -> str:
    now_ts = _now_ts()
    token = secrets.token_urlsafe(18)
    path = _cache_path()
    cache = _load_cache(path)
    tokens = _pruned_tokens(cache.get("tokens", {}), now_ts)
    tokens[token] = {
        "fingerprint": route_fingerprint(
            role=role,
            title=title,
            prompt=prompt,
            artifacts=artifacts,
            workspace=workspace,
            goal=goal,
        ),
        "created_at": now_ts,
        "expires_at": now_ts + _ttl_seconds(),
        "source": str(source or ""),
        "confidence": float(confidence or 0.0),
    }
    cache["tokens"] = tokens
    _save_cache(path, cache)
    return token


def validate_route_token(
    token: str,
    *,
    role: str,
    title: str,
    prompt: str,
    artifacts: Iterable[Any] | None,
    workspace: str | None,
    goal: str | None,
) -> RouteTokenCheck:
    cleaned = str(token or "").strip()
    if not cleaned:
        return RouteTokenCheck(False, "missing route token")
    cache = _load_cache(_cache_path())
    entry = cache.get("tokens", {}).get(cleaned)
    if not isinstance(entry, dict):
        return RouteTokenCheck(False, "route token not found")
    try:
        expires_at = int(entry.get("expires_at", 0))
    except (TypeError, ValueError):
        return RouteTokenCheck(False, "route token has invalid expiry")
    if expires_at <= _now_ts():
        return RouteTokenCheck(False, "route token expired")
    expected = route_fingerprint(
        role=role,
        title=title,
        prompt=prompt,
        artifacts=artifacts,
        workspace=workspace,
        goal=goal,
    )
    if entry.get("fingerprint") != expected:
        return RouteTokenCheck(False, "route token does not match this capability")
    return RouteTokenCheck(True, "matched recent LLM router output")
