#!/usr/bin/env python3
"""Lightweight per-project runtime initialization for global installs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Mapping

from project_paths import CLAUDE_DIR, REPO_ROOT, normalize_external_path, parse_env_file


PROJECT_MARKER = "cc-router-project.json"
STALE_HOOK_SCRIPT_NAMES = (
    "hook_intercept_create.py",
    "hook_user_prompt_submit.py",
    "hook_session_start.py",
    "hook_stop_focus.py",
)
RUNTIME_GITIGNORE = """# cc-router-codex project runtime
.env
taskctl.sqlite3*
artifacts/
task-plans/
logs/
"""


@dataclass(frozen=True)
class ProjectRuntime:
    workspace: Path
    claude_dir: Path
    env_path: Path
    db_path: Path
    route_cache_path: Path
    initialized: bool
    full_install: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolve_workspace(value: str | Path | None) -> Path:
    return Path(normalize_external_path(value or ".")).expanduser().resolve(strict=False)


def is_full_install(workspace: Path) -> bool:
    return (workspace / ".claude" / "scripts" / "taskctl.py").is_file()


def _hook_entry_references_stale_project_script(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = str(hook.get("command") or "").replace("\\", "/")
        if ".claude/scripts/" in command and any(name in command for name in STALE_HOOK_SCRIPT_NAMES):
            return True
    return False


def sanitize_stale_project_settings(claude_dir: Path) -> bool:
    """Remove stale project-level cc-router hooks from lightweight runtimes.

    Older project installs may leave `.claude/settings.json` pointing at
    project-local hook scripts. Global installs intentionally do not copy those
    scripts into each workspace, so those stale hooks break future Claude
    sessions before the global hooks can route work.
    """

    settings_path = claude_dir / "settings.json"
    if not settings_path.exists() or (claude_dir / "scripts" / "taskctl.py").exists():
        return False
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False

    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return False

    changed = False
    sanitized_hooks: dict[str, object] = {}
    for event_name, entries in hooks.items():
        if not isinstance(entries, list):
            sanitized_hooks[event_name] = entries
            continue
        kept = [entry for entry in entries if not _hook_entry_references_stale_project_script(entry)]
        if len(kept) != len(entries):
            changed = True
        if kept:
            sanitized_hooks[event_name] = kept

    if not changed:
        return False

    backup_path = settings_path.with_name(f"settings.stale-hooks-{utc_now().replace(':', '').replace('-', '')}.bak")
    settings_path.replace(backup_path)
    if sanitized_hooks:
        payload["hooks"] = sanitized_hooks
    else:
        payload.pop("hooks", None)
    settings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def runtime_for(workspace: str | Path | None) -> ProjectRuntime:
    root = resolve_workspace(workspace)
    claude_dir = root / ".claude"
    return ProjectRuntime(
        workspace=root,
        claude_dir=claude_dir,
        env_path=claude_dir / ".env",
        db_path=claude_dir / "taskctl.sqlite3",
        route_cache_path=claude_dir / "task-plans" / "route-cache.json",
        initialized=(claude_dir / PROJECT_MARKER).is_file() or is_full_install(root),
        full_install=is_full_install(root),
    )


def _project_env_lines(global_values: Mapping[str, str]) -> list[str]:
    values = dict(global_values)
    values["TASKCTL_PROJECT_RUNTIME"] = "1"
    values["TASKCTL_PROJECT_INITIALIZED_AT"] = utc_now()
    values["TASKCTL_GLOBAL_CONTROL_PLANE"] = str(CLAUDE_DIR)
    ordered = [
        "TASKCTL_PROJECT_RUNTIME",
        "TASKCTL_PROJECT_INITIALIZED_AT",
        "TASKCTL_GLOBAL_CONTROL_PLANE",
        "TASKCTL_INSTALL_OS",
        "TASKCTL_PYTHON",
        "CODEX_BIN",
        "TASKCTL_ROUTER_PROVIDER",
        "TASKCTL_ROUTER_CODEX_MODEL",
        "TASKCTL_ROUTER_CODEX_REASONING_EFFORT",
        "TASKCTL_ROUTER_CODEX_TIMEOUT",
        "TASKCTL_ROUTER_CODEX_FALLBACK",
        "TASKCTL_INPUT_GUARD_PROVIDER",
        "TASKCTL_INPUT_GUARD_CODEX_MODEL",
        "TASKCTL_INPUT_GUARD_CODEX_REASONING_EFFORT",
        "TASKCTL_INPUT_GUARD_CODEX_TIMEOUT",
        "TASKCTL_INPUT_GUARD_CODEX_FALLBACK",
        "TASKCTL_BASH_GUARD",
        "TASKCTL_BASH_GUARD_PROVIDER",
        "TASKCTL_BASH_GUARD_CODEX_MODEL",
        "TASKCTL_BASH_GUARD_CODEX_REASONING_EFFORT",
        "TASKCTL_BASH_GUARD_CODEX_TIMEOUT",
        "ASSETGEN_CODEX_SANDBOX",
        "ASSETGEN_CODEX_MODEL",
        "ASSETGEN_CODEX_TIMEOUT",
        "ASSETGEN_PROMPT_MCP_TARGET",
        "ASSETGEN_PROMPT_MCP_REPO",
        "ASSETGEN_PROMPT_MCP_REF",
        "ASSETGEN_PROMPT_MCP_TIMEOUT",
        "ASSETGEN_PROMPT_MCP_INSTALL_TIMEOUT",
        "ASSETGEN_PROMPT_MCP_VERSION_TIMEOUT",
        "ASSETGEN_PROMPT_MCP_LATEST_TTL_SECONDS",
        "ASSETGEN_PROMPT_TEMPLATE_TOP",
    ]
    keys = [key for key in ordered if key in values and str(values[key]).strip()]
    seen = set(keys)
    keys.extend(sorted(key for key in values if key not in seen and str(values[key]).strip()))
    return [
        "# Generated by cc-router-codex project auto-init.",
        "# Project-specific overrides may be added here.",
        *[f"{key}={values[key]}" for key in keys],
        "",
    ]


def ensure_project_initialized(workspace: str | Path | None, *, source: str = "auto") -> ProjectRuntime:
    runtime = runtime_for(workspace)
    if runtime.full_install:
        return runtime

    if runtime.workspace.exists() and not runtime.workspace.is_dir():
        raise OSError(f"workspace is not a directory: {runtime.workspace}")
    runtime.workspace.mkdir(parents=True, exist_ok=True)
    runtime.claude_dir.mkdir(parents=True, exist_ok=True)
    sanitize_stale_project_settings(runtime.claude_dir)
    (runtime.claude_dir / "artifacts").mkdir(exist_ok=True)
    (runtime.claude_dir / "task-plans").mkdir(exist_ok=True)

    gitignore = runtime.claude_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(RUNTIME_GITIGNORE, encoding="utf-8")

    if not runtime.env_path.exists():
        runtime.env_path.write_text("\n".join(_project_env_lines(parse_env_file(CLAUDE_DIR / ".env"))), encoding="utf-8")

    marker_path = runtime.claude_dir / PROJECT_MARKER
    if not marker_path.exists():
        marker = {
            "schemaVersion": 1,
            "initializedAt": utc_now(),
            "source": source,
            "workspace": str(runtime.workspace),
            "globalControlPlane": str(CLAUDE_DIR),
            "mutableState": [
                ".claude/.env",
                ".claude/taskctl.sqlite3*",
                ".claude/artifacts/",
                ".claude/task-plans/",
            ],
        }
        marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return runtime_for(runtime.workspace)


def _set_project_env(key: str, value: Path | str, *, override_explicit: bool = False) -> None:
    marker = f"{key}_AUTOINIT"
    if override_explicit or key not in os.environ or os.environ.get(marker) == "1":
        os.environ[key] = str(value)
        os.environ[marker] = "1"


def apply_project_environment(workspace: str | Path | None, *, set_db: bool = True) -> ProjectRuntime:
    runtime = ensure_project_initialized(workspace)
    _set_project_env("TASKCTL_WORKSPACE", runtime.workspace, override_explicit=True)
    if set_db:
        _set_project_env("TASKCTL_DB", runtime.db_path)
    _set_project_env("TASKCTL_ROUTE_CACHE_PATH", runtime.route_cache_path)
    _set_project_env("TASKCTL_ROUTER_ENV_PATH", runtime.env_path)
    model_policy = runtime.claude_dir / "model_policy.json"
    if model_policy.exists():
        _set_project_env("TASKCTL_MODEL_POLICY_PATH", model_policy)
    return runtime


def current_working_runtime() -> ProjectRuntime | None:
    cwd = Path.cwd().resolve(strict=False)
    if cwd == REPO_ROOT:
        return None
    if (cwd / ".claude").is_dir():
        return runtime_for(cwd)
    return None
