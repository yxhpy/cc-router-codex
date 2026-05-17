#!/usr/bin/env python3
"""Shared helpers for Claude/Grok hook payload context."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from project_paths import normalize_external_path


WORKSPACE_KEYS = (
    "cwd",
    "workspace",
    "workspaceRoot",
    "workspace_root",
    "workingDirectory",
    "working_directory",
    "projectDir",
    "project_dir",
    "projectPath",
    "project_path",
)

GROK_TOOL_ALIASES = {
    "run_terminal_cmd": "Bash",
    "run_terminal_command": "Bash",
    "search_replace": "Edit",
    "write": "Write",
    "write_file": "Write",
    "task": "Task",
    "spawn_subagent": "Task",
}

GROK_PAYLOAD_TOOL_NAMES = {
    "run_terminal_cmd",
    "run_terminal_command",
    "search_replace",
    "write",
    "write_file",
}


def _normalize_workspace(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = normalize_external_path(value.strip())
    try:
        return str(Path(text).expanduser().resolve(strict=False))
    except OSError:
        return text


def raw_hook_event(payload: dict[str, Any]) -> str:
    for key in ("hookEventName", "hook_event_name", "event", "eventName", "event_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = os.environ.get("GROK_HOOK_EVENT")
    return value.strip() if value else ""


def is_grok_hook(payload: dict[str, Any]) -> bool:
    event = raw_hook_event(payload)
    tool_name = payload.get("tool_name")
    tool_name_key = tool_name.strip() if isinstance(tool_name, str) else ""
    return (
        bool(os.environ.get("GROK_HOOK_EVENT"))
        or "toolName" in payload
        or "toolInput" in payload
        or tool_name_key in GROK_PAYLOAD_TOOL_NAMES
        or ("_" in event and event.lower() == event)
    )


def hook_tool_name(payload: dict[str, Any]) -> str:
    value = payload.get("tool_name")
    if not isinstance(value, str) or not value.strip():
        value = payload.get("toolName")
    if not isinstance(value, str) or not value.strip():
        return ""
    name = value.strip()
    return GROK_TOOL_ALIASES.get(name.lower(), name)


def hook_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("tool_input", "toolInput"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def target_workspace(payload: dict[str, Any]) -> str:
    """Return the project workspace that invoked the hook.

    The control-plane scripts can live in a fixed repository, but worker tasks
    must run against the invoking session's project directory.
    """

    for key in WORKSPACE_KEYS:
        workspace = _normalize_workspace(payload.get(key))
        if workspace:
            return workspace

    tool_input = hook_tool_input(payload)
    for key in WORKSPACE_KEYS:
        workspace = _normalize_workspace(tool_input.get(key))
        if workspace:
            return workspace

    return str(Path.cwd().resolve(strict=False))
