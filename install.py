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
RUNTIME_DIR_NAMES = {"__pycache__", "artifacts", "task-plans", "logs"}
RUNTIME_FILE_NAMES = {".env", "settings.local.json", "ALLOW_CONTROL_PLANE_WRITES"}
RUNTIME_SUFFIXES = {".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".log", ".tmp", ".bak"}


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


def detect_system() -> Detection:
    return Detection(
        system=platform.system().lower() or "unknown",
        python=str(Path(sys.executable).resolve()) if sys.executable else "python",
        codex=shutil.which("codex") or shutil.which("codex.cmd") or shutil.which("codex.exe") or "",
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
    for name in (".claude", "CLAUDE.md"):
        path = target / name
        if path.exists():
            conflicts.append(path)
    return conflicts


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
            "ASSETGEN_CODEX_SANDBOX": values.get("ASSETGEN_CODEX_SANDBOX", "workspace"),
            "ASSETGEN_CODEX_TIMEOUT": values.get("ASSETGEN_CODEX_TIMEOUT", "900"),
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
        "ASSETGEN_CODEX_SANDBOX",
        "ASSETGEN_CODEX_TIMEOUT",
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
    if any(char.isspace() for char in text) or '"' in text:
        return '"' + text.replace('"', '\\"') + '"'
    return text


def hook_command(python_cmd: str, script_name: str) -> str:
    return f"{command_arg(python_cmd)} .claude/scripts/{script_name}"


def rewrite_settings(settings_path: Path, detection: Detection) -> None:
    if not settings_path.exists():
        return
    payload = json.loads(settings_path.read_text(encoding="utf-8", errors="replace"))
    replacements = {
        "hook_intercept_create.py": hook_command(detection.python, "hook_intercept_create.py"),
        "hook_user_prompt_submit.py": hook_command(detection.python, "hook_user_prompt_submit.py"),
        "hook_session_start.py": hook_command(detection.python, "hook_session_start.py"),
    }
    hooks = payload.get("hooks", {})
    if isinstance(hooks, dict):
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
    if not source_claude.is_dir() or not source_claude_md.is_file():
        raise SystemExit(f"ERROR: source is missing .claude or CLAUDE.md: {source}")

    conflicts = conflicts_for(target)
    if conflicts and not yes and not confirm_overwrite(conflicts, input_func=input_func, output_func=output_func):
        raise SystemExit("ABORTED: existing .claude or CLAUDE.md was not overwritten")

    for path in conflicts:
        safe_remove(path, target)

    shutil.copytree(source_claude, target / ".claude", ignore=copy_ignore)
    shutil.copy2(source_claude_md, target / "CLAUDE.md")
    (target / ".claude" / "artifacts").mkdir(exist_ok=True)
    (target / ".claude" / "task-plans").mkdir(exist_ok=True)

    detection = detect_system()
    env_path = target / ".claude" / ".env"
    env_path.write_text(render_env(source, detection), encoding="utf-8")
    settings_path = target / ".claude" / "settings.json"
    rewrite_settings(settings_path, detection)

    copied = (".claude", "CLAUDE.md", ".claude/.env")
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
