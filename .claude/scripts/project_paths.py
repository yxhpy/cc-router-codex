#!/usr/bin/env python3
"""Shared path helpers for the Claude/Codex control plane."""

from __future__ import annotations

import os
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = SCRIPT_DIR.parent
REPO_ROOT = CLAUDE_DIR.parent


def repo_root() -> Path:
    return REPO_ROOT


def claude_dir() -> Path:
    return CLAUDE_DIR


def script_path(name: str) -> Path:
    return SCRIPT_DIR / name


def display_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def resolve_in_repo(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def repo_relative(path_value: str | Path) -> str:
    path = resolve_in_repo(path_value)
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return display_path(path)


def parse_env_file(path: Path | None = None) -> dict[str, str]:
    env_path = path or CLAUDE_DIR / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def command_arg(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return '""'
    if any(char.isspace() for char in text) or '"' in text:
        return '"' + text.replace('"', '\\"') + '"'
    return text


def python_command() -> str:
    value = os.environ.get("TASKCTL_PYTHON") or parse_env_file().get("TASKCTL_PYTHON") or "python"
    return command_arg(value)


def script_command(name: str) -> str:
    return f"{python_command()} {command_arg(display_path(script_path(name)))}"
