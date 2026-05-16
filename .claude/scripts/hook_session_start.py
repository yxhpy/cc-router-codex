#!/usr/bin/env python3
"""
SessionStart hook - injects control-plane rules into the main model context.
"""

import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
CLAUDE_DIR = SCRIPT_DIR.parent

from claude_write_policy import CONTROL_PLANE_WRITE_MARKER, marker_expiry, marker_state
from project_paths import script_command

BASE_RULES = f"""## Codex Task Control Rules

These rules are mandatory. They do not depend on explicit skill invocation.

1. ds v4 is the control plane only: choose exactly one bounded capability input at a time, inspect state, and decide whether to continue, stop, or ask the user.
2. Do not implement, test, review, or close work directly in ds context.
2a. Claude Code permission prompts are bypassed by default with `permissions.defaultMode=bypassPermissions`; policy enforcement is handled by project hooks, especially PreToolUse and Stop.
3. All production work must be represented in SQLite through `{script_command('taskctl.py')}`.
4. Use `taskctl.py capability` as the normal production entrypoint. It validates one main-model-authored prompt, stores one SQLite job/task, executes one Codex worker, records expected artifacts, and returns. Generated commands must pass the repository root as `--workspace`.
5. Codex workers own planning, divergence, requirements, prototype, UI/UX, asset generation, debugging, operations, security, documentation, release, full-stack implementation, tests, review, and closure.
6. LLM routing may suggest a role composition, but it is advisory only. Execute one capability, inspect the result, then choose the next capability; do not enqueue the whole composition.
7. Every Codex-bound task input must be authored by the main model and validated by `capability`; do not manually split normal work into submit/filter/enqueue/run-next commands.
8. Use `taskctl.py status` or `taskctl.py audit` only after a capability command returns or fails. Use `taskctl.py audit --quality` for debugger/planner/uiux/reviewer/closer Markdown report artifacts. Do not use fixed workflows, import-plan, run-job, or dependency chains.
8a. When command syntax is unclear, run `taskctl.py command <name> --workspace <project>` or `taskctl.py doctor --workspace <project>` instead of guessing or reading source. Hooks include directly executable `next_command` / `command_contract` fields and a `replacement_command` template on blocks.
9. A worker is successful only when required artifacts are recorded and their files exist; Windows sandbox failures are retryable failures, not completion.
10. Role boundaries are enforced before Codex can run: planner/divergent/requirements/reviewer/closer/debugger/security are analysis-only, uiux is design-only, prototype is spec-only, assetgen is image-asset-only, operator owns operational work only, docs owns documentation only, release owns versioning/release artifacts only, fullstack is the only product-code role, and tester may write reports/screenshots/test files but no production source.
11. Frontend UI/UX workers must use project design specs first; if none exist, run `.claude/scripts/sync_design_refs.py --offline --quiet`, consult local `.claude/design-references`, and record `design_reference_selection` plus `style_contract` before implementation. Selected design decisions must be traceable; do not add untraceable beautification. Prefer project/open-license images or video for real visuals. If needed media is missing, UI/UX should record an `asset_generation_brief`; assetgen must generate local raster assets through `.claude/scripts/assetgen_exec.py`, first fast-checking/installing the local `image-2-prompt` MCP with `.claude/scripts/prompt_template_mcp.py`, comparing installed/latest MCP git commit versions and warning if an upgrade may be needed, retrieving prompt templates, using `gpt-5.4-mini`, recording `local_asset_manifest`, and never using SVG as a generated asset fallback.
11a. CONTEXT.md and docs/adr/ are optional project-owned soft inputs. Workers should read them when present before vocabulary, naming, architecture, persistence, API, deployment, dependency, storage, or hard-to-reverse decisions. Their absence is not a blocker, and workers must not create them unless the user explicitly asked for project context docs or an ADR.
12. UserPromptSubmit routing, semantic task-input guarding, and ambiguous Bash command review are LLM-backed through `.claude/scripts/llm_router.py`, using `.claude/.env` provider/model settings. Codex CLI `gpt-5.4-mini` with an output schema is preferred for stable JSON decisions. Bash PreToolUse first blocks deterministic direct-write patterns and allows known lifecycle commands such as package-manager install/build/test; only ambiguous commands reach the model guard. Hook-generated commands may include `--route-token` to reuse the exact recent LLM route instead of running a duplicate classifier; deterministic role/artifact checks and `safety_filter.py` still run. Do not replace semantic routing or role-boundary judgment with regex/keyword rules.
13. Production goals activate `.claude/task-plans/focus_state.json`. The Stop hook blocks final answers until `focus_guard.py complete` records result evidence or `focus_guard.py exhausted` records all attempted routes and blockers. After failures, record attempts, inspect logs/artifacts, search or try another viable route, and continue by default.
14. Worker tasks record reusable lessons with `taskctl.py experience-add`; closer/reviewer tasks curate them and regenerate the compact learned-experience skill. Add atom metadata such as topic, skill, source command/path, or failure signature when useful. Use `experience-stale` for contradicted lessons and `experience-sync-skill --min-confidence 4` when broad reuse should exclude weak accepted lessons.
14a. Bundled skill publication is governed by .claude/skill-manifest.json. Draft, deprecated, and private skills must not be published under .claude/skills/ or .claude/plugins/*/skills/. After editing bundled skills or plugin bridges, run `python .claude/scripts/skill_manifest_check.py` in an installed target, or `python tools/skill_manifest_check.py` in the source repository.
15. Use `taskctl.py status` and `taskctl.py audit` for compact state summaries instead of reading full logs by default. For failed, blocked, or handed-off jobs, use `taskctl.py checkpoint-save`, `checkpoint-list`, `checkpoint-restore`, and `checkpoint-report` so retries start from recorded blockers, missing artifacts, next role, and resume hints.
16. `.claude` is not a general write escape hatch. Claude may directly write runtime state such as `.claude/artifacts/**`, `.claude/task-plans/**`, and `.claude/scheduled_tasks.json`; control-plane source/config writes require explicit maintenance mode; product files still go through Codex/taskctl.
17. Add new control-plane behavior as focused modules under `.claude/scripts/` instead of adding unrelated responsibilities to the legacy `taskctl.py` monolith."""


def read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def main():
    mandatory = read_optional(CLAUDE_DIR / "MANDATORY_CONTEXT.md")
    context = BASE_RULES if not mandatory else BASE_RULES + "\n\n" + mandatory
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
