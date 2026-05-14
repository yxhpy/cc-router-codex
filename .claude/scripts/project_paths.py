#!/usr/bin/env python3
"""Shared path helpers for the Claude/Codex control plane."""

from __future__ import annotations

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
