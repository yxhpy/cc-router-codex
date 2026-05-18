#!/usr/bin/env python3
"""Install the cc-router-codex Claude/Codex control plane into a project.

Run from any target project with:
  python C:/path/to/cc-router-codex/install.py

The installer copies this repository's distributable `.claude` control plane
and `CLAUDE.md`, generates `.claude/.env` for the local machine, and prompts
before overwriting an existing installation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import sys
from typing import Callable, Iterable


SOURCE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR_NAMES = {"__pycache__", "artifacts", "task-plans", "logs", ".prompt-searcher"}
RUNTIME_FILE_NAMES = {".env", "settings.local.json", "ALLOW_CONTROL_PLANE_WRITES"}
RUNTIME_SUFFIXES = {".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".log", ".tmp", ".bak"}
CONTROL_PLANE_HOOK_SCRIPTS = (
    "hook_intercept_create.py",
    "hook_user_prompt_submit.py",
    "hook_session_start.py",
    "hook_stop_focus.py",
)
DEFAULT_BASH_ALLOW_RULES = (
    "Bash(python *)",
    "Bash(python3 *)",
    "Bash(py *)",
    "Bash(codex *)",
    "Bash(codex.cmd *)",
    "Bash(codex.exe *)",
    # Kept for older Claude Code versions and existing installs.
    "Bash(python:*)",
    "Bash(codex:*)",
)
DEFAULT_PERMISSION_MODE = "bypassPermissions"
PRE_TOOL_USE_MATCHER = ""


@dataclass(frozen=True)
class Detection:
    system: str
    python: str
    codex: str


@dataclass(frozen=True)
class InstallResult:
    target: Path
    copied: tuple[str, ...]
    env_path: Path
    settings_path: Path
    detection: Detection


def now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_command_path(value: str | Path) -> str:
    return str(value or "").strip().replace("\\", "/")


def resolved_command_path(value: str | None) -> str:
    if not value:
        return ""
    try:
        return normalize_command_path(Path(value).resolve())
    except OSError:
        return normalize_command_path(value)


def detect_system() -> Detection:
    codex = shutil.which("codex") or shutil.which("codex.cmd") or shutil.which("codex.exe") or ""
    return Detection(
        system=platform.system().lower() or "unknown",
        python=resolved_command_path(sys.executable) if sys.executable else "python",
        codex=resolved_command_path(codex),
    )


def should_exclude(name: str, is_dir: bool) -> bool:
    path = Path(name)
    if is_dir and path.name in RUNTIME_DIR_NAMES:
        return True
    if path.name in RUNTIME_FILE_NAMES:
        return True
    if path.name.startswith("taskctl.sqlite3"):
        return True
    return path.suffix.lower() in RUNTIME_SUFFIXES


def copy_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        # shutil.copytree calls ignore before stat copying; use a best-effort
        # directory check while keeping suffix/name rules deterministic.
        candidate = Path(_directory) / name
        if should_exclude(name, candidate.is_dir()):
            ignored.add(name)
    return ignored


def ensure_target_directory(path: Path) -> Path:
    target = path.expanduser().resolve()
    if target.exists() and not target.is_dir():
        raise SystemExit(f"ERROR: target is not a directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    return target


def conflicts_for(target: Path) -> list[Path]:
    conflicts: list[Path] = []
    for name in (".claude", "CLAUDE.md", "VERSION", "VERSIONING.md"):
        path = target / name
        if path.exists():
            conflicts.append(path)
    return conflicts


def is_user_level_target(target: Path) -> bool:
    try:
        return target.expanduser().resolve() == Path.home().resolve()
    except OSError:
        return False


def confirm_overwrite(
    conflicts: Iterable[Path],
    *,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> bool:
    conflict_list = list(conflicts)
    if not conflict_list:
        return True
    output_func("Existing Claude control-plane files will be overwritten:")
    for path in conflict_list:
        output_func(f"  - {path}")
    answer = input_func("Type y to overwrite and continue: ").strip().lower()
    return answer == "y"


def safe_remove(path: Path, target: Path) -> None:
    resolved = path.resolve()
    root = target.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SystemExit(f"ERROR: refusing to remove path outside target: {resolved}") from exc
    if resolved == root:
        raise SystemExit(f"ERROR: refusing to remove target root: {resolved}")
    if resolved.is_dir():
        shutil.rmtree(resolved)
    elif resolved.exists():
        resolved.unlink()


def read_env_template(source_root: Path) -> dict[str, str]:
    template = source_root / ".claude" / ".env.example"
    values: dict[str, str] = {}
    if not template.exists():
        return values
    for line in template.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def render_env(source_root: Path, detection: Detection) -> str:
    values = read_env_template(source_root)
    values.update(
        {
            "TASKCTL_PYTHON": detection.python,
            "TASKCTL_INSTALL_OS": detection.system,
            "TASKCTL_INSTALL_GENERATED_AT": now_stamp(),
            "TASKCTL_ROUTER_PROVIDER": "codex",
            "TASKCTL_INPUT_GUARD_PROVIDER": "codex",
            "TASKCTL_ROUTER_CODEX_FALLBACK": "1",
            "TASKCTL_INPUT_GUARD_CODEX_FALLBACK": "1",
            "TASKCTL_BASH_GUARD": values.get("TASKCTL_BASH_GUARD", "llm"),
            "TASKCTL_BASH_GUARD_PROVIDER": "codex",
            "TASKCTL_BASH_GUARD_CODEX_MODEL": values.get("TASKCTL_BASH_GUARD_CODEX_MODEL", "gpt-5.4-mini"),
            "TASKCTL_BASH_GUARD_CODEX_REASONING_EFFORT": values.get("TASKCTL_BASH_GUARD_CODEX_REASONING_EFFORT", "low"),
            "TASKCTL_BASH_GUARD_CODEX_TIMEOUT": values.get("TASKCTL_BASH_GUARD_CODEX_TIMEOUT", "45"),
            "ASSETGEN_CODEX_SANDBOX": values.get("ASSETGEN_CODEX_SANDBOX", "workspace"),
            "ASSETGEN_CODEX_MODEL": values.get("ASSETGEN_CODEX_MODEL", "gpt-5.4-mini"),
            "ASSETGEN_CODEX_TIMEOUT": values.get("ASSETGEN_CODEX_TIMEOUT", "900"),
            "ASSETGEN_PROGRESS_HEARTBEAT_SECONDS": values.get("ASSETGEN_PROGRESS_HEARTBEAT_SECONDS", "60"),
            "ASSETGEN_PROMPT_MCP_TARGET": values.get("ASSETGEN_PROMPT_MCP_TARGET", ".prompt-searcher"),
            "ASSETGEN_PROMPT_MCP_REPO": values.get("ASSETGEN_PROMPT_MCP_REPO", "https://github.com/yxhpy/image-2-prompt"),
            "ASSETGEN_PROMPT_MCP_REF": values.get("ASSETGEN_PROMPT_MCP_REF", "main"),
            "ASSETGEN_PROMPT_MCP_TIMEOUT": values.get("ASSETGEN_PROMPT_MCP_TIMEOUT", "8"),
            "ASSETGEN_PROMPT_MCP_INSTALL_TIMEOUT": values.get("ASSETGEN_PROMPT_MCP_INSTALL_TIMEOUT", "300"),
            "ASSETGEN_PROMPT_MCP_VERSION_TIMEOUT": values.get("ASSETGEN_PROMPT_MCP_VERSION_TIMEOUT", "5"),
            "ASSETGEN_PROMPT_MCP_LATEST_TTL_SECONDS": values.get("ASSETGEN_PROMPT_MCP_LATEST_TTL_SECONDS", "3600"),
            "ASSETGEN_PROMPT_TEMPLATE_TOP": values.get("ASSETGEN_PROMPT_TEMPLATE_TOP", "3"),
        }
    )
    if detection.codex:
        values["CODEX_BIN"] = detection.codex

    preferred_order = [
        "TASKCTL_INSTALL_OS",
        "TASKCTL_INSTALL_GENERATED_AT",
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
        "ASSETGEN_PROGRESS_HEARTBEAT_SECONDS",
        "ASSETGEN_PROMPT_MCP_TARGET",
        "ASSETGEN_PROMPT_MCP_REPO",
        "ASSETGEN_PROMPT_MCP_REF",
        "ASSETGEN_PROMPT_MCP_TIMEOUT",
        "ASSETGEN_PROMPT_MCP_INSTALL_TIMEOUT",
        "ASSETGEN_PROMPT_MCP_VERSION_TIMEOUT",
        "ASSETGEN_PROMPT_MCP_LATEST_TTL_SECONDS",
        "ASSETGEN_PROMPT_TEMPLATE_TOP",
    ]
    ordered_keys = [key for key in preferred_order if key in values]
    ordered_keys.extend(sorted(key for key in values if key not in set(ordered_keys)))
    lines = [
        "# Generated by cc-router-codex install.py.",
        "# Re-run the installer after moving this project to another machine.",
    ]
    for key in ordered_keys:
        lines.append(f"{key}={values[key]}")
    return "\n".join(lines).rstrip() + "\n"


def command_arg(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return '""'
    if any(char.isspace() for char in text) or any(char in text for char in '"()&;<>|'):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def hook_command(
    python_cmd: str,
    script_path: str | Path,
    *,
    scripts_dir: Path | None = None,
    system: str | None = None,
) -> str:
    runner = scripts_dir / "run_python.cmd" if scripts_dir is not None else None
    install_system = (system or platform.system()).lower()
    if install_system == "windows" and runner is not None and runner.exists():
        return f"{command_arg(normalize_command_path(runner))} {command_arg(normalize_command_path(script_path))}"
    return f"{command_arg(python_cmd)} {command_arg(normalize_command_path(script_path))}"


def bash_allow_rule(command_prefix: str) -> str:
    return f"Bash({command_arg(normalize_command_path(command_prefix))} *)"


def install_bash_allow_rules(detection: Detection) -> tuple[str, ...]:
    rules = list(DEFAULT_BASH_ALLOW_RULES)
    for command in (detection.python, detection.codex):
        if command:
            rules.append(bash_allow_rule(command))
    return tuple(dict.fromkeys(rules))


def ensure_permission_allows(payload: dict[str, object], detection: Detection) -> None:
    permissions = payload.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        payload["permissions"] = permissions

    allow = permissions.get("allow")
    if not isinstance(allow, list):
        allow = []
        permissions["allow"] = allow

    permissions["defaultMode"] = DEFAULT_PERMISSION_MODE

    seen = {entry for entry in allow if isinstance(entry, str)}
    for rule in install_bash_allow_rules(detection):
        if rule not in seen:
            allow.append(rule)
            seen.add(rule)


def rewrite_settings(settings_path: Path, detection: Detection) -> None:
    if not settings_path.exists():
        return
    payload = json.loads(settings_path.read_text(encoding="utf-8", errors="replace"))
    ensure_permission_allows(payload, detection)
    scripts_dir = settings_path.parent / "scripts"
    runner = scripts_dir / "run_python.cmd"
    if detection.system == "windows" and runner.exists():
        permissions = payload.setdefault("permissions", {})
        if isinstance(permissions, dict):
            allow = permissions.setdefault("allow", [])
            if isinstance(allow, list):
                runner_rule = bash_allow_rule(str(runner))
                if runner_rule not in allow:
                    allow.append(runner_rule)
    replacements = {
        "hook_intercept_create.py": hook_command(detection.python, scripts_dir / "hook_intercept_create.py", scripts_dir=scripts_dir, system=detection.system),
        "hook_user_prompt_submit.py": hook_command(detection.python, scripts_dir / "hook_user_prompt_submit.py", scripts_dir=scripts_dir, system=detection.system),
        "hook_session_start.py": hook_command(detection.python, scripts_dir / "hook_session_start.py", scripts_dir=scripts_dir, system=detection.system),
        "hook_stop_focus.py": hook_command(detection.python, scripts_dir / "hook_stop_focus.py", scripts_dir=scripts_dir, system=detection.system),
    }
    hooks = payload.get("hooks", {})
    if isinstance(hooks, dict):
        pre_tool_entries = hooks.get("PreToolUse")
        if isinstance(pre_tool_entries, list):
            for entry in pre_tool_entries:
                if isinstance(entry, dict):
                    entry["matcher"] = PRE_TOOL_USE_MATCHER
        for entries in hooks.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for hook in entry.get("hooks", []):
                    if not isinstance(hook, dict):
                        continue
                    command = str(hook.get("command") or "")
                    for script_name, replacement in replacements.items():
                        if script_name in command:
                            hook["command"] = replacement
    settings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def hook_entry_uses_control_plane(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = str(hook.get("command") or "")
        if any(script_name in command for script_name in CONTROL_PLANE_HOOK_SCRIPTS):
            return True
    return False


def merge_settings_payload(
    *,
    existing: dict[str, object],
    base: dict[str, object],
) -> dict[str, object]:
    merged = dict(existing)

    base_plugins = base.get("enabledPlugins")
    if isinstance(base_plugins, dict):
        existing_plugins = merged.get("enabledPlugins")
        plugins = dict(existing_plugins) if isinstance(existing_plugins, dict) else {}
        plugins.update(base_plugins)
        merged["enabledPlugins"] = plugins

    base_permissions = base.get("permissions")
    if "permissions" not in merged and isinstance(base_permissions, dict):
        merged["permissions"] = base_permissions

    base_hooks = base.get("hooks")
    if isinstance(base_hooks, dict):
        existing_hooks = merged.get("hooks")
        hooks = dict(existing_hooks) if isinstance(existing_hooks, dict) else {}
        for event_name, event_entries in base_hooks.items():
            if not isinstance(event_entries, list):
                continue
            current_entries = hooks.get(event_name)
            kept_entries = [
                entry
                for entry in (current_entries if isinstance(current_entries, list) else [])
                if not hook_entry_uses_control_plane(entry)
            ]
            hooks[event_name] = list(event_entries) + kept_entries
        merged["hooks"] = hooks

    for key, value in base.items():
        if key not in merged:
            merged[key] = value
    return merged


def copy_claude_tree(source_claude: Path, target_claude: Path, *, merge: bool) -> None:
    if not merge:
        shutil.copytree(source_claude, target_claude, ignore=copy_ignore)
        return

    target_claude.mkdir(parents=True, exist_ok=True)
    for child in source_claude.iterdir():
        if should_exclude(child.name, child.is_dir()):
            continue
        destination = target_claude / child.name
        if child.name == "settings.json":
            continue
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True, ignore=copy_ignore)
        else:
            shutil.copy2(child, destination)


def install_settings(source_settings: Path, target_settings: Path, *, merge: bool) -> None:
    if not merge:
        return
    base = load_json_object(source_settings)
    existing = load_json_object(target_settings)
    merged = merge_settings_payload(existing=existing, base=base)
    target_settings.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def install_control_plane(
    *,
    source_root: Path = SOURCE_ROOT,
    target: Path,
    yes: bool = False,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> InstallResult:
    source = source_root.expanduser().resolve()
    target = ensure_target_directory(target)
    if source == target:
        raise SystemExit("ERROR: target cannot be the cc-router-codex source repository")

    source_claude = source / ".claude"
    source_claude_md = source / "CLAUDE.md"
    source_version = source / "VERSION"
    source_versioning = source / "VERSIONING.md"
    if not source_claude.is_dir() or not source_claude_md.is_file():
        raise SystemExit(f"ERROR: source is missing .claude or CLAUDE.md: {source}")

    conflicts = conflicts_for(target)
    if conflicts and not yes and not confirm_overwrite(conflicts, input_func=input_func, output_func=output_func):
        raise SystemExit("ABORTED: existing .claude, CLAUDE.md, VERSION, or VERSIONING.md was not overwritten")

    merge_user_claude = is_user_level_target(target)
    if not merge_user_claude:
        for path in conflicts:
            safe_remove(path, target)

    copy_claude_tree(source_claude, target / ".claude", merge=merge_user_claude)
    install_settings(source_claude / "settings.json", target / ".claude" / "settings.json", merge=merge_user_claude)
    shutil.copy2(source_claude_md, target / "CLAUDE.md")
    if source_version.is_file():
        shutil.copy2(source_version, target / "VERSION")
    if source_versioning.is_file():
        shutil.copy2(source_versioning, target / "VERSIONING.md")
    (target / ".claude" / "artifacts").mkdir(exist_ok=True)
    (target / ".claude" / "task-plans").mkdir(exist_ok=True)

    detection = detect_system()
    env_path = target / ".claude" / ".env"
    env_path.write_text(render_env(source, detection), encoding="utf-8")
    settings_path = target / ".claude" / "settings.json"
    rewrite_settings(settings_path, detection)

    copied_items = [".claude", "CLAUDE.md"]
    if source_version.is_file():
        copied_items.append("VERSION")
    if source_versioning.is_file():
        copied_items.append("VERSIONING.md")
    copied_items.append(".claude/.env")
    copied = tuple(copied_items)
    return InstallResult(target=target, copied=copied, env_path=env_path, settings_path=settings_path, detection=detection)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install cc-router-codex into a target project.")
    parser.add_argument("--target", default=".", help="Target project directory. Defaults to the current directory.")
    parser.add_argument("-y", "--yes", action="store_true", help="Overwrite existing .claude and CLAUDE.md without prompting.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = install_control_plane(target=Path(args.target), yes=args.yes)
    print(f"Installed cc-router-codex into: {result.target}")
    print(f"Detected OS: {result.detection.system}")
    print(f"Python: {result.detection.python}")
    print(f"Codex: {result.detection.codex or '<not found on PATH>'}")
    print(f"Generated env: {result.env_path}")
    print("Next command from the target project:")
    print(f"  {command_arg(result.detection.python)} .claude/scripts/taskctl.py status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
