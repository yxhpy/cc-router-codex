#!/usr/bin/env python3
"""Shared helpers for Claude hook payload context."""

from __future__ import annotations

from pathlib import Path
from typing import Any


WORKSPACE_KEYS = (
    "cwd",
    "workspace",
    "workingDirectory",
    "working_directory",
    "projectDir",
    "project_dir",
    "projectPath",
    "project_path",
)


def _normalize_workspace(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return str(Path(value.strip()).expanduser().resolve(strict=False))
    except OSError:
        return value.strip()


def target_workspace(payload: dict[str, Any]) -> str:
    """Return the project workspace that invoked the hook.

    The control-plane scripts can live in a fixed repository, but worker tasks
    must run against the Claude session's project directory.
    """

    for key in WORKSPACE_KEYS:
        workspace = _normalize_workspace(payload.get(key))
        if workspace:
            return workspace

    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for key in WORKSPACE_KEYS:
            workspace = _normalize_workspace(tool_input.get(key))
            if workspace:
                return workspace

    return str(Path.cwd().resolve(strict=False))
