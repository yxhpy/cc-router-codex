#!/usr/bin/env python3
"""
SessionStart hook - injects control-plane rules into the main model context.
"""

import json
import os
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
CLAUDE_DIR = SCRIPT_DIR.parent

from claude_write_policy import CONTROL_PLANE_WRITE_MARKER, marker_expiry, marker_state
from hook_context import target_workspace
import project_init
from project_paths import script_command

COMPACT_RULES = f"""## Codex Task Control Rules

Mandatory compact policy for this session:

- ds v4 is the controller only. Do not implement, test, review, or close production work directly in this context.
- Production work must go through one bounded taskctl capability at a time:
  `{script_command('taskctl.py')} capability --role <role> --title "<title>" --prompt "<bounded worker prompt>" --artifact <kind:path> --workspace <target> --goal "<goal>"`
- Generated taskctl commands must use the active target project as `--workspace`; inspect `taskctl.py status` / `taskctl.py audit` after a capability returns before choosing the next step.
- If syntax is unclear, run `{script_command('taskctl.py')} command capability --workspace <target>` or `doctor`; hook block responses include executable replacement commands.
- Direct product writes, direct workspace data processing, and ad hoc multi-step workflows are blocked. Direct Claude writes are only for runtime state such as `.claude/artifacts/**`, `.claude/task-plans/**`, and `.claude/scheduled_tasks.json`.
- Role boundaries are enforced by taskctl before Codex runs. `fullstack` owns product code; `tester` owns reports/tests; UI/UX, prototype, asset, review, docs, release, security, operations, and closure tasks stay in their own bounded roles.
- Frontend work must use project design sources first; when missing, route UI/UX/style selection before implementation. Generated visual assets must be local raster files with a recorded manifest.
- Production goals may activate the Stop focus guard; complete with `{script_command('focus_guard.py')} complete --workspace <target> --evidence "<evidence>"` only after required artifacts exist."""

FULL_RULES = f"""{COMPACT_RULES}

## Expanded Control Rules

1. Use `taskctl.py capability` as the normal production entrypoint. It validates one main-model-authored prompt, stores one SQLite job/task, executes one Codex worker, records expected artifacts, and returns.
2. For long capability prompts, write a UTF-8 prompt file under the active target workspace's `.claude/task-plans/` directory with the file tool, then pass `--prompt-file .claude/task-plans/<name>.txt`. Do not use shell heredocs, redirection, `tee`, or `/tmp` prompt files.
3. LLM routing may suggest a role composition, but it is advisory only. Execute one capability, inspect the result, then choose the next capability; do not enqueue the whole composition.
4. A worker is successful only when required artifacts are recorded and their files exist; Windows sandbox failures are retryable failures, not completion.
5. UserPromptSubmit routing, semantic task-input guarding, and ambiguous Bash command review are LLM-backed through `.claude/scripts/llm_router.py`.
6. Worker tasks record reusable lessons with `taskctl.py experience-add`; reviewer/closer tasks curate them and regenerate the compact learned-experience skill."""


def read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def read_hook_json() -> dict[str, object]:
    raw = sys.stdin.buffer.read()
    if not raw:
        return {}
    for encoding in ("utf-8", "gb18030"):
        try:
            parsed = json.loads(raw.decode(encoding))
            if isinstance(parsed, dict):
                return parsed
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return {}


def session_context_profile() -> str:
    value = os.environ.get("TASKCTL_SESSION_CONTEXT_PROFILE", "compact").strip().lower()
    return "full" if value == "full" else "compact"


def base_context() -> str:
    if session_context_profile() != "full":
        return COMPACT_RULES
    mandatory = read_optional(CLAUDE_DIR / "MANDATORY_CONTEXT.md")
    return FULL_RULES if not mandatory else FULL_RULES + "\n\n" + mandatory


def main():
    payload = read_hook_json()
    workspace = target_workspace(payload) if payload else str(Path.cwd().resolve(strict=False))
    runtime = project_init.apply_project_environment(workspace, set_db=False)
    context = base_context()
    if runtime.initialized:
        context += (
            "\n\n## Project Runtime\n"
            f"Project runtime is initialized at `{runtime.claude_dir}`. "
            "Mutable state for this workspace uses project-local `.claude/.env`, "
            "`.claude/taskctl.sqlite3`, `.claude/artifacts`, and `.claude/task-plans`."
        )
    state = marker_state()
    if state in {"active", "expired", "invalid"}:
        expiry = marker_expiry()
        detail = expiry.isoformat().replace("+00:00", "Z") if expiry else state
        context += (
            "\n\n## Control-Plane Maintenance Marker\n"
            f"{CONTROL_PLANE_WRITE_MARKER} is {state} ({detail}). "
            "Remove it after maintenance; expired or invalid markers do not allow writes."
        )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
