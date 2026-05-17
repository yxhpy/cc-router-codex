#!/usr/bin/env python3
"""Safe in-place update for old projects that have locked files in artifacts/.

This avoids rmtree on the whole .claude (which fails on locked logs).
It only refreshes:
  - .claude/scripts/ (new + updated .py files)
  - .claude/settings.json (absolute hooks + latest python path)
  - .claude/.env
  - Root CLAUDE.md, VERSION, VERSIONING.md

Run from anywhere:
  python tools/safe-inplace-update.py
"""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
SOURCE_CLAUDE = SOURCE_ROOT / ".claude"
SOURCE_SCRIPTS = SOURCE_CLAUDE / "scripts"

PYTHON_EXE = Path(sys.executable).resolve(strict=False)
NORMALIZED_PYTHON = PYTHON_EXE.as_posix()

STALE_PROJECTS = [
    r"C:\Users\Administrator\Desktop\demo01",
    r"C:\Users\Administrator\Desktop\demo02",
    r"C:\Users\Administrator\Desktop\gpt-image-2",
    r"C:\Users\Administrator\Desktop\qqgamemj",
]

def now_stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def hook_command(script_name: str, target_scripts: Path) -> str:
    script_path = target_scripts / script_name
    runner = target_scripts / "run_python.cmd"
    if runner.exists():
        return f'"{runner.as_posix()}" "{script_path.as_posix()}"'
    return f'"{NORMALIZED_PYTHON}" "{script_path.as_posix()}"'

def rewrite_settings_json(settings_path: Path, target_scripts: Path) -> None:
    if not settings_path.exists():
        print(f"  WARN: no settings.json at {settings_path}")
        return

    data = json.loads(settings_path.read_text(encoding="utf-8", errors="replace"))

    replacements = {
        "hook_intercept_create.py": hook_command("hook_intercept_create.py", target_scripts),
        "hook_user_prompt_submit.py": hook_command("hook_user_prompt_submit.py", target_scripts),
        "hook_session_start.py": hook_command("hook_session_start.py", target_scripts),
        "hook_stop_focus.py": hook_command("hook_stop_focus.py", target_scripts),
    }

    changed = 0
    hooks = data.get("hooks", {})
    if isinstance(hooks, dict):
        pre_tool = hooks.get("PreToolUse")
        if isinstance(pre_tool, list):
            for entry in pre_tool:
                if isinstance(entry, dict):
                    entry["matcher"] = ""
        for event_list in hooks.values():
            if not isinstance(event_list, list):
                continue
            for entry in event_list:
                if not isinstance(entry, dict):
                    continue
                for h in entry.get("hooks", []):
                    if not isinstance(h, dict):
                        continue
                    cmd = str(h.get("command") or "")
                    for script_name, new_cmd in replacements.items():
                        if script_name in cmd:
                            h["command"] = new_cmd
                            h["statusMessage"] = h.get("statusMessage", "")
                            changed += 1

    # Ensure permissions and defaultMode
    perms = data.setdefault("permissions", {})
    perms["defaultMode"] = "bypassPermissions"
    allow = perms.setdefault("allow", [])
    for rule in [
        "Bash(python *)", "Bash(python3 *)", "Bash(py *)",
        "Bash(codex *)", "Bash(codex.cmd *)", "Bash(codex.exe *)",
        f'Bash("{NORMALIZED_PYTHON}" *)',
        f'Bash("{(target_scripts / "run_python.cmd").as_posix()}" *)',
    ]:
        if rule not in allow:
            allow.append(rule)

    # Ensure the Stop hook (focus_guard) exists even if the old install was missing it
    stop_cmd = hook_command("hook_stop_focus.py", target_scripts)
    if "Stop" not in hooks or not isinstance(hooks.get("Stop"), list):
        hooks["Stop"] = [{
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": stop_cmd,
                "timeout": 10,
                "statusMessage": "Enforcing active-goal focus..."
            }]
        }]
        changed += 1
        print("  + Injected missing Stop hook (focus_guard)")
    else:
        # make sure the command inside is up to date
        for entry in hooks.get("Stop", []):
            for h in entry.get("hooks", []):
                if isinstance(h, dict) and "hook_stop_focus.py" in str(h.get("command", "")):
                    h["command"] = stop_cmd
                    changed += 1

    settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  Rewrote settings.json ({changed} hook commands updated)")

def update_one_project(target_str: str) -> bool:
    target = Path(target_str)
    if not target.is_dir():
        print(f"SKIP: {target} does not exist")
        return False

    claude_dir = target / ".claude"
    if not claude_dir.is_dir():
        print(f"SKIP: {target} has no .claude (not a control-plane project)")
        return False

    print(f"\n=== Updating {target.name} ===")

    target_scripts = claude_dir / "scripts"
    target_scripts.mkdir(parents=True, exist_ok=True)

    # 1. Refresh scripts/ (safe, does not touch artifacts/)
    if SOURCE_SCRIPTS.is_dir():
        # Remove old .pyc in target first (they are not locked usually)
        for pyc in target_scripts.rglob("*.pyc"):
            try:
                pyc.unlink()
            except Exception:
                pass
        shutil.copytree(SOURCE_SCRIPTS, target_scripts, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"  Synced scripts/ ({len(list(target_scripts.glob('*.py')))} .py files)")
    else:
        print("  ERROR: source scripts missing")
        return False

    # 2. Rewrite settings.json with absolute hooks
    settings_path = claude_dir / "settings.json"
    rewrite_settings_json(settings_path, target_scripts)

    # 3. Fresh .env (simple render)
    env_path = claude_dir / ".env"
    env_content = f"""# Generated by safe-inplace-update.py at {now_stamp()}
TASKCTL_PYTHON={NORMALIZED_PYTHON}
TASKCTL_INSTALL_OS=windows
TASKCTL_INSTALL_GENERATED_AT={now_stamp()}
TASKCTL_ROUTER_PROVIDER=codex
TASKCTL_INPUT_GUARD_PROVIDER=codex
TASKCTL_ROUTER_CODEX_FALLBACK=1
"""
    env_path.write_text(env_content, encoding="utf-8")
    print(f"  Wrote fresh .env")

    # 4. Root files (CLAUDE.md + version files)
    for fname in ("CLAUDE.md", "VERSION", "VERSIONING.md"):
        src = SOURCE_ROOT / fname
        if src.exists():
            shutil.copy2(src, target / fname)
    print(f"  Updated CLAUDE.md / VERSION")

    print(f"  -> SUCCESS for {target.name}")
    return True

def main():
    print("Safe in-place control plane updater (for projects with locked artifacts logs)")
    print(f"Source: {SOURCE_ROOT}")
    print(f"Python: {NORMALIZED_PYTHON}")
    print(f"Targets: {len(STALE_PROJECTS)} old projects")

    success = 0
    for proj in STALE_PROJECTS:
        if update_one_project(proj):
            success += 1

    print(f"\n=== Done: {success}/{len(STALE_PROJECTS)} projects updated in-place ===")
    print("Now re-open the projects in Claude Code / Grok. The hooks should trigger with latest code.")
    print("Your previous artifacts/ logs / task-plans are preserved.")

if __name__ == "__main__":
    main()
