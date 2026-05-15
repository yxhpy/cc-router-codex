#!/usr/bin/env python3
"""Run the full local verification suite for the taskctl control plane."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "scripts"
VALIDATOR = Path.home() / ".codex" / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py"


def run(args: list[str], label: str, quiet: bool = False) -> None:
    print(f"== {label}", flush=True)
    stdout = subprocess.DEVNULL if quiet else None
    result = subprocess.run(args, cwd=str(ROOT), text=True, stdout=stdout)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all taskctl verification checks")
    parser.add_argument("--real-codex", action="store_true", help="include a real readonly Codex wrapper smoke")
    parser.add_argument("--real-claude-cli", action="store_true", help="include real Claude CLI hook/routing/tool checks")
    args = parser.parse_args()

    py = sys.executable
    tests = [
        "test_hooks.py",
        "test_taskctl.py",
        "test_task_input_filter.py",
        "test_llm_router.py",
        "test_model_policy.py",
        "test_codex_exec.py",
        "test_assetgen_exec.py",
        "test_install.py",
        "test_sync_design_refs.py",
        "test_safety_filter.py",
    ]
    for test in tests:
        run([py, "-B", str(SCRIPTS / test)], test)

    e2e = [py, "-B", str(SCRIPTS / "test_end_to_end_flow.py")]
    if args.real_codex:
        e2e.append("--real-codex")
    run(e2e, "test_end_to_end_flow.py" + (" --real-codex" if args.real_codex else ""))

    if args.real_claude_cli:
        run([py, "-B", str(SCRIPTS / "test_claude_cli_flow.py")], "test_claude_cli_flow.py")

    run([py, "-B", "-m", "py_compile", str(ROOT / "install.py"), *[str(path) for path in SCRIPTS.glob("*.py")]], "py_compile")
    run([py, "-m", "json.tool", str(ROOT / ".claude" / "settings.json")], "settings.json", quiet=True)
    run([py, "-m", "json.tool", str(ROOT / ".claude" / "model_policy.json")], "model_policy.json", quiet=True)

    if VALIDATOR.exists():
        run([py, str(VALIDATOR), str(ROOT / ".claude" / "skills" / "learned-experience")], "learned-experience skill")
        plugin_skill = ROOT / ".claude" / "plugins" / "task-decompose" / "skills" / "learned-experience"
        if plugin_skill.exists():
            run([py, str(VALIDATOR), str(plugin_skill)], "plugin learned-experience skill")

    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
