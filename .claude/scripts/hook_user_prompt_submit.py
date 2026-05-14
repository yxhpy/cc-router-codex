#!/usr/bin/env python3
"""UserPromptSubmit hook that asks the fast LLM router for taskctl routing."""

from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import llm_router
from project_paths import REPO_ROOT, script_path


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


def extract_prompt(payload: dict[str, object]) -> str:
    for key in ("prompt", "userPrompt", "message", "input"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def ps_quote(value: str) -> str:
    return '"' + str(value or "").replace('"', '`"') + '"'


def artifact_args(artifacts: list[str]) -> str:
    if not artifacts:
        return "--artifact <kind:path>"
    return " ".join(f"--artifact {ps_quote(item)}" for item in artifacts)


def suggested_command(route: llm_router.Route, prompt: str) -> str:
    return (
        f"python {ps_quote(str(script_path('taskctl.py')))} capability "
        f"--role {route.role} "
        f"--title {ps_quote(route.title)} "
        f"--prompt {ps_quote(route.worker_prompt)} "
        f"{artifact_args(route.artifacts)} "
        f"--workspace {ps_quote(str(REPO_ROOT))} "
        f"--goal {ps_quote(prompt)}"
    )


def composition_context(route: llm_router.Route) -> str:
    if not route.steps:
        return "- 1. " + route.role + ": " + route.title
    lines = []
    for index, step in enumerate(route.steps, 1):
        artifacts = ", ".join(step.artifacts) if step.artifacts else "<decide exact artifact>"
        purpose = f" - {step.purpose}" if step.purpose else ""
        lines.append(f"- {index}. {step.role}: {step.title}{purpose}; artifacts: {artifacts}")
    return "\n".join(lines)


def routing_context(prompt: str, route: llm_router.Route) -> str:
    route_note = f"Router source: {route.source}; confidence: {route.confidence:.2f}; reason: {route.reason}"
    if route.error:
        route_note += f"; router_error: {route.error}"
    return f"""## Mandatory LLM Task Routing

The OpenAI SDK LLM router classified this user request as production work.
Do not directly write/edit product files from the controller context.

{route_note}

Required action: run exactly one atomic capability command through SQLite.
Required command name: taskctl.py capability.

LLM-suggested capability composition, not a fixed workflow:
{composition_context(route)}

Run only the first/next capability now. After it returns, inspect the artifact
and audit result, then decide whether to run, skip, or revise later suggested
capabilities. Do not enqueue the whole composition.

Recommended next tool call is Bash with:
{suggested_command(route, prompt)}

Do not call Write, Edit, MultiEdit, or shell file-writing commands before this
control-plane command.

`capability` validates the main-model-authored prompt, stores one SQLite job/task,
executes one Codex worker, auto-records expected artifact paths when files exist,
and prints the audit result. Use `status` or `audit` only after the command
returns or fails.

Role boundaries are enforced by the Python filter before Codex can run:
planner/divergent/requirements/reviewer/closer are analysis-only, uiux is
design-only, prototype is spec-only, fullstack is the only production-code role,
and tester may write reports/screenshots/test files but no production source.

Do not use fixed workflows, import-plan, run-job, dependency chains, or manual
submit/filter/enqueue/run command sequences. How to combine capabilities is
decided only by the main model after each single-step execution.

Frontend capability steps must still enforce project design specs first, or
selected DESIGN.md references with traceable style mapping, open-license/project media,
generated local bitmap assets when media is missing, and sparse SVG usage.

For frontend/UI work, UI/UX must use project design sources first. If none
exist, it must run `sync_design_refs.py --offline --quiet` and select from local
`.claude/design-references`; implementation
must follow the selected `design_reference_selection`/`style_contract` and must
not invent untraceable visual styling. If visual media is missing, UI/UX should
record `asset_generation_brief`; implementation should create/place local
bitmap assets, record `local_asset_manifest`, and avoid remote hotlinks.
"""


def main() -> int:
    payload = read_hook_json()
    prompt = extract_prompt(payload)
    if not prompt:
        print(json.dumps({"continue": True}))
        return 0

    route = llm_router.route_prompt(prompt)
    if not route.production_work:
        print(json.dumps({"continue": True}))
        return 0

    print(json.dumps({
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": routing_context(prompt, route),
        },
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
