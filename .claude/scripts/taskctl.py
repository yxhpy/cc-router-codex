#!/usr/bin/env python3
"""
taskctl.py - Python/SQLite control plane for Codex worker tasks.

The main model only submits and monitors jobs. Planning, divergence,
requirements, prototype, UI/UX, asset generation, full-stack implementation,
debugging, operations, security, documentation, release, testing, review, and
closure are represented as worker tasks stored in SQLite and executed by Codex
through this controller.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import artifact_quality
import model_policy
import command_catalog
from project_paths import REPO_ROOT, script_command
import route_cache
from task_input_filter import normalize_required_artifacts, require_valid_task_input, validate_task_input
import worker_runner


SCRIPT_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = SCRIPT_DIR.parent
DEFAULT_DB = CLAUDE_DIR / "taskctl.sqlite3"
ATOMIC_WORKFLOW = "atomic"

ROLES = (
    "planner",
    "divergent",
    "requirements",
    "prototype",
    "uiux",
    "assetgen",
    "debugger",
    "operator",
    "security",
    "docs",
    "release",
    "fullstack",
    "tester",
    "reviewer",
    "closer",
)
EXPERIENCE_KINDS = (
    "pattern",
    "pitfall",
    "script",
    "skill_fix",
    "quality_rule",
    "tooling",
    "architecture",
    "frontend",
    "testing",
)
EXPERIENCE_STATUSES = {
    "candidate",
    "accepted",
    "rejected",
    "superseded",
    "stale",
}
EXPERIENCE_FOOTER_MARKER = "[TASKCTL EXPERIENCE CAPTURE]"
ROLE_BOUNDARY_MARKER = "[TASKCTL ROLE BOUNDARY]"
FRONTEND_DESIGN_MARKER = "[TASKCTL FRONTEND DESIGN SOURCE]"
PROJECT_CONTEXT_MARKER = "[TASKCTL PROJECT CONTEXT]"
PROJECT_CONTEXT_TEMPLATE_MARKER = "[TASKCTL PROJECT CONTEXT TEMPLATE]"
REQUIRED_ARTIFACT_MARKER = "[TASKCTL REQUIRED ARTIFACTS]"
ASSETGEN_IMAGE_KINDS = {"image", "asset", "sprite", "texture", "icon", "thumbnail", "key_art", "overlay", "render"}
ASSETGEN_RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
ROLE_BOUNDARIES = {
    "planner": "Produce plans, inventories, sequencing notes, or architecture notes only. Do not create or modify product code.",
    "divergent": "Produce options, tradeoff analysis, and alternatives only. Do not create or modify product code.",
    "requirements": "Produce requirements, acceptance checks, and constraints only. Do not create or modify product code.",
    "uiux": "Produce design artifacts only, such as style inventory, design reference selection, component map, style contract, or visual review notes. Do not create HTML/CSS/JS/TSX/backend/schema/migration files.",
    "prototype": "Produce prototype specifications, DOM/interaction contracts, and behavior notes only. Do not create production UI code.",
    "assetgen": "Produce or place local raster image assets only through .claude/scripts/assetgen_exec.py. That script must fast-check/install the local image-2-prompt MCP, compare installed/latest MCP git commit versions and warn if an upgrade may be needed, retrieve prompt templates, and use gpt-5.4-mini before raster generation. Assetgen covers game sprites, icons, textures, web visuals, video thumbnails, key art, overlays, asset_generation_brief, or local_asset_manifest. Do not create SVG, HTML/CSS/JS/TSX/backend/schema/migration files.",
    "debugger": "Reproduce failures, inspect logs, isolate root cause, and produce debugging reports or minimal fix recommendations only. Do not patch product code.",
    "operator": "Handle installs, dependencies, builds, CI, Docker, packaging, deploys, runtime health, and operational runbooks. Do not create or modify product source code.",
    "security": "Produce security reviews, threat models, dependency audits, permission analyses, and remediation plans only. Do not patch product code.",
    "docs": "Create or update documentation, runbooks, API notes, README material, and changelog prose only. Do not modify product source code.",
    "release": "Own versioning, CHANGELOG/release notes, tags, release packaging, install verification, rollback notes, and release audit artifacts. Do not patch product source code.",
    "fullstack": "You may create or modify product implementation code for frontend, backend, database, scripts, and production HTML as requested.",
    "tester": "Produce verification reports, screenshots, and test files under test paths only. Do not modify production source files.",
    "reviewer": "Produce review findings and risk reports only. Do not patch product code.",
    "closer": "Produce closure and audit summaries only. Do not patch product code.",
}
TASK_STATUSES = {
    "queued",
    "running",
    "done",
    "blocked",
    "canceled",
    "failed_retryable",
    "failed_terminal",
}
DEFAULT_RUN_TIMEOUT_SECONDS = 1800
DEFAULT_STALE_AFTER_SECONDS = 900

def default_workspace() -> str:
    return str(REPO_ROOT.resolve())


def prepare_workspace(value: str | None) -> str:
    try:
        return str(worker_runner.ensure_workspace(value or default_workspace()))
    except OSError as exc:
        raise SystemExit(f"ERROR: invalid workspace: {exc}") from exc


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    workspace TEXT NOT NULL,
    constraints_json TEXT NOT NULL DEFAULT '[]',
    acceptance_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    depends_on TEXT NOT NULL DEFAULT '[]',
    required_artifacts_json TEXT NOT NULL DEFAULT '[]',
    result_summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    command TEXT NOT NULL,
    log_path TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    stdout_summary TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,
    issues_json TEXT NOT NULL DEFAULT '[]',
    tests_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    workflow TEXT NOT NULL DEFAULT 'general',
    role TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    reuse_hint TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    confidence INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'candidate',
    source_path TEXT NOT NULL DEFAULT '',
    supersedes_id INTEGER REFERENCES experiences(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_experiences_status ON experiences(status);
CREATE INDEX IF NOT EXISTS idx_experiences_workflow_role ON experiences(workflow, role);
CREATE INDEX IF NOT EXISTS idx_experiences_job_task ON experiences(job_id, task_id);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    next_role TEXT NOT NULL DEFAULT '',
    next_command TEXT NOT NULL DEFAULT '',
    resume_hint TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_job ON checkpoints(job_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_created ON checkpoints(created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_seconds(value: str, at: datetime | None = None) -> int | None:
    started = parse_timestamp(value)
    if started is None:
        return None
    at = at or datetime.now(timezone.utc)
    return max(0, int((at - started).total_seconds()))


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{rem:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def db_path(value: str | None = None) -> Path:
    raw = value or os.environ.get("TASKCTL_DB") or str(DEFAULT_DB)
    return Path(raw).expanduser().resolve()


def connect(path_value: str | None = None) -> sqlite3.Connection:
    path = db_path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "required_artifacts_json" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN required_artifacts_json TEXT NOT NULL DEFAULT '[]'")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA user_version = 1")
    return conn


def json_list(values: Iterable[str] | None) -> str:
    return json.dumps(list(values or []), ensure_ascii=False)


def parse_json_list(value: str) -> list[Any]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("expected a JSON list")
    return parsed


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def artifact_dir(job_id: int) -> str:
    return f".claude/artifacts/job-{job_id}"


def checkpoint_dir(workspace: str) -> Path:
    return Path(workspace) / ".claude" / "task-plans" / "checkpoints"


def emit_event(
    conn: sqlite3.Connection,
    event_type: str,
    message: str,
    job_id: int | None = None,
    task_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO events(job_id, task_id, type, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (job_id, task_id, event_type, message, now()),
    )


def load_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise SystemExit(f"ERROR: job not found: {job_id}")
    return row


def load_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise SystemExit(f"ERROR: task not found: {task_id}")
    return row




def compact_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def normalize_tags(values: Iterable[str] | None) -> list[str]:
    tags: list[str] = []
    for value in values or []:
        for item in re.split(r"[,;\s]+", str(value).strip().lower()):
            if item and item not in tags:
                tags.append(item[:40])
    return tags


def task_id_shell_reference() -> tuple[str, str]:
    if os.name == "nt":
        return "PowerShell", "$env:TASKCTL_TASK_ID"
    return "Shell", "$TASKCTL_TASK_ID"


def experience_footer() -> str:
    shell_label, task_id_ref = task_id_shell_reference()
    return f"""

{EXPERIENCE_FOOTER_MARKER}
Before finishing, decide whether this task produced reusable experience.
Record only specific, evidence-backed lessons that can help future tasks. Do
not record generic advice, secrets, credentials, or noisy observations.

If useful, add one or more candidate lessons:
{shell_label}: {script_command('taskctl.py')} experience-add --task-id {task_id_ref} --kind pattern --title "Short title" --summary "What was learned" --evidence "Artifact, file, command, or failure that proved it" --reuse "When and how to reuse it" --tag workflow --tag area

Use kind values such as pattern, pitfall, script, skill_fix, quality_rule,
tooling, architecture, frontend, or testing. Keep each lesson concise. If no
reusable lesson exists, add nothing.
"""


def attach_experience_footer(prompt: str) -> str:
    if EXPERIENCE_FOOTER_MARKER in prompt:
        return prompt
    return prompt.rstrip() + experience_footer()


def role_boundary_footer(role: str) -> str:
    boundary = ROLE_BOUNDARIES.get(role)
    if not boundary:
        return ""
    return f"""

{ROLE_BOUNDARY_MARKER}
Role: {role}
Boundary: {boundary}
If the requested work would cross this boundary, stop and report the mismatch
instead of doing another role's work.
"""


def frontend_design_footer() -> str:
    return f"""

{FRONTEND_DESIGN_MARKER}
If this task involves frontend UI, app pages, marketing/sample pages, visual
style, high-fidelity screens, icons, images, or interaction polish, design
source traceability is mandatory:
- First use project-local sources if present: DESIGN.md, design tokens, theme
  files, component docs, Storybook, screenshots, or existing components.
- If no project design source exists and your role is uiux, run:
  python .claude/scripts/sync_design_refs.py --offline --quiet
  Then select suitable references from .claude/design-references/manifest.json
  and the referenced DESIGN.md files. Record design_reference_selection and
  style_contract artifacts. Every color, type scale, spacing, surface,
  component state, motion, icon/media choice, and density decision must be
  traceable to the selected project/reference source. Do not add untraceable
  model beautification.
- If your role is prototype, assetgen, fullstack, tester, or reviewer, use the
  project design source or prior design_reference_selection/style_contract artifacts.
  For a new visual frontend without those sources, stop and report that a uiux
  capability is required before implementation instead of inventing style.
- Prefer project/open-license high-quality images or video for real product,
  place, people, or atmosphere visuals. Assetgen outputs must be local raster
  files generated through `.claude/scripts/assetgen_exec.py`; assetgen first
  uses `.claude/scripts/prompt_template_mcp.py` to fast-check or install the
  local `image-2-prompt` MCP, compare installed/latest MCP git commit versions
  and warn if an upgrade may be needed, retrieve prompt templates, and use
  `gpt-5.4-mini` for the bounded prompt adapter. Do not use SVG as an assetgen
  fallback. SVG remains limited to manually coded icons, logos, diagrams, or
  tiny functional marks when the design source requires it.
- If suitable local/open-license media is missing, generated bitmap assets are
  an allowed localization path. UI/UX tasks should record an
  asset_generation_brief with prompts, style constraints, dimensions, and
  intended local file paths. Assetgen tasks should generate the bitmap files
  under a local project asset directory with `.claude/scripts/assetgen_exec.py`,
  record a local_asset_manifest, and reference local paths only. Fullstack
  tasks then consume those local assets rather than inventing or hotlinking
  media.
"""


def project_context_footer() -> str:
    return f"""

{PROJECT_CONTEXT_MARKER}
Optional project context sources are soft inputs:
- If CONTEXT.md exists, read it before choosing project vocabulary, domain
  terms, naming, or user-facing language.
- If docs/adr/ exists, check relevant ADRs before architecture, persistence,
  API, dependency, storage, deployment, or hard-to-reverse naming decisions.
- Treat these files as guidance, not a blocker. If they are absent, continue
  with the task using current repository evidence.
- Do not create CONTEXT.md or docs/adr/ unless the user explicitly requested
  project context documentation or an ADR.
"""


def project_context_template_footer(role: str) -> str:
    if role != "docs":
        return ""
    return f"""

{PROJECT_CONTEXT_TEMPLATE_MARKER}
Only create or update CONTEXT.md or docs/adr/ files when the user explicitly requested it.
This template applies only when the user explicitly requested it.
When requested, use these lightweight shapes:
- CONTEXT.md: project purpose, domain vocabulary, naming conventions,
  important user/workflow terms, and links to authoritative docs.
- docs/adr/YYYY-MM-DD-short-title.md: status, context, decision,
  consequences, alternatives considered, and verification evidence.
Do not create these files as a side effect of unrelated documentation work.
"""


def required_artifact_kinds_for_workflow(workflow: str) -> tuple[str, ...]:
    _ = workflow
    return ()


def required_roles_for_workflow(workflow: str) -> tuple[str, ...]:
    _ = workflow
    return ()


def workflow_artifacts_complete(conn: sqlite3.Connection, job_id: int) -> bool:
    _ = conn, job_id
    return True


def artifact_spec_parts(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    kind, separator, path = raw.partition(":")
    if not separator:
        return kind.strip(), ""
    return kind.strip(), path.strip().strip('"')


def artifact_kinds(values: Iterable[str] | None) -> list[str]:
    kinds: list[str] = []
    for value in values or []:
        kind, _ = artifact_spec_parts(str(value))
        if kind and kind not in kinds:
            kinds.append(kind)
    return kinds


def artifact_specs_from_json(value: str | None) -> list[tuple[str, str]]:
    specs: list[tuple[str, str]] = []
    for item in parse_json_list(value or "[]"):
        kind, path = artifact_spec_parts(str(item))
        if kind:
            specs.append((kind, path))
    return specs


def artifact_contract_footer(required_artifacts: Iterable[str] | None) -> str:
    specs = [(kind, path) for kind, path in (artifact_spec_parts(str(item)) for item in (required_artifacts or [])) if kind]
    if not specs:
        return ""
    shell_label, task_id_ref = task_id_shell_reference()
    lines = [
        "",
        "[TASKCTL REQUIRED ARTIFACTS]",
        "Before finishing, create or verify each required artifact and record it in SQLite.",
        "Use the exact artifact kind and path below; do not invent a different output path.",
    ]
    for kind, path in specs:
        if path:
            lines.append(f"- {kind}: {path}")
            lines.append(
                f'  {shell_label}: {script_command("taskctl.py")} artifact {task_id_ref} --kind {kind} --path "{path}" --summary "{kind} artifact"'
            )
        else:
            lines.append(f"- {kind}: choose the correct produced file path and record it")
            lines.append(
                f'  {shell_label}: {script_command("taskctl.py")} artifact {task_id_ref} --kind {kind} --path "<path>" --summary "{kind} artifact"'
            )
    return "\n".join(lines) + "\n"


def attach_task_footers(prompt: str, role: str, required_artifacts: Iterable[str] | None = None) -> str:
    body = prompt.rstrip()
    if ROLE_BOUNDARY_MARKER not in body:
        body += role_boundary_footer(role)
    if FRONTEND_DESIGN_MARKER not in body:
        body += frontend_design_footer()
    if PROJECT_CONTEXT_MARKER not in body:
        body += project_context_footer()
    if PROJECT_CONTEXT_TEMPLATE_MARKER not in body:
        body += project_context_template_footer(role)
    body += artifact_contract_footer(required_artifacts)
    return attach_experience_footer(body)


def insert_task(
    conn: sqlite3.Connection,
    job_id: int,
    role: str,
    title: str,
    prompt: str,
    depends_on: list[int] | None = None,
    status: str = "queued",
    required_artifacts: list[str] | None = None,
) -> int:
    if role not in ROLES:
        raise SystemExit(f"ERROR: unsupported role: {role}")
    if status not in TASK_STATUSES:
        raise SystemExit(f"ERROR: unsupported task status: {status}")
    stamp = now()
    cur = conn.execute(
        """
        INSERT INTO tasks(job_id, role, title, prompt, status, depends_on, required_artifacts_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            role,
            title,
            attach_task_footers(prompt, role, required_artifacts),
            status,
            json.dumps(depends_on or []),
            json.dumps(required_artifacts or [], ensure_ascii=False),
            stamp,
            stamp,
        ),
    )
    task_id = int(cur.lastrowid)
    emit_event(conn, "task_created", f"{role}: {title}", job_id=job_id, task_id=task_id)
    return task_id


def refresh_job_status(conn: sqlite3.Connection, job_id: int) -> str:
    tasks = conn.execute("SELECT role, status FROM tasks WHERE job_id = ?", (job_id,)).fetchall()
    workflow = job_workflow(conn, job_id)
    required_roles = required_roles_for_workflow(workflow)
    if not tasks:
        status = "queued"
    else:
        statuses = {row["status"] for row in tasks}
        roles = {row["role"] for row in tasks}
        has_required_loop = all(role in roles for role in required_roles)
        if statuses == {"done"} and has_required_loop:
            status = "done" if workflow_artifacts_complete(conn, job_id) else "blocked"
        elif statuses == {"done"}:
            status = "ready"
        elif "running" in statuses:
            status = "running"
        elif "failed_terminal" in statuses:
            status = "failed_terminal"
        elif "canceled" in statuses and statuses <= {"done", "canceled"}:
            status = "canceled"
        elif "blocked" in statuses:
            status = "blocked"
        elif "failed_retryable" in statuses:
            status = "failed_retryable"
        else:
            status = "ready"
    conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", (status, now(), job_id))
    conn.commit()
    return status




def submit_atomic_job(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        workspace = prepare_workspace(args.workspace)
        stamp = now()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO jobs(goal, status, priority, workspace, constraints_json, acceptance_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    args.goal,
                    "queued",
                    args.priority,
                    workspace,
                    json_list(args.constraint),
                    json_list(args.acceptance),
                    stamp,
                    stamp,
                ),
            )
            job_id = int(cur.lastrowid)
            emit_event(conn, "workflow_selected", ATOMIC_WORKFLOW, job_id=job_id)
            emit_event(conn, "job_submitted", "debug job submitted without tasks; normal production uses capability", job_id=job_id)
            refresh_job_status(conn, job_id)
            job = load_job(conn, job_id)

    payload = {
        "job_id": job_id,
        "db": str(db_path(args.db)),
        "status": job["status"],
        "workflow": ATOMIC_WORKFLOW,
        "tasks": 0,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"JOB {job_id} {job['status']} [{ATOMIC_WORKFLOW}]")
        print(f"DB: {db_path(args.db)}")
        print(f"DEBUG ONLY: normal production should use {script_command('taskctl.py')} capability ...")



def submit_auto_job(args: argparse.Namespace) -> None:
    submit_atomic_job(args)


def create_capability_job(
    conn: sqlite3.Connection,
    goal: str,
    workspace: str | None,
    priority: str,
    constraints: Iterable[str] | None = None,
    acceptance: Iterable[str] | None = None,
) -> int:
    stamp = now()
    cur = conn.execute(
        """
        INSERT INTO jobs(goal, status, priority, workspace, constraints_json, acceptance_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            goal,
            "queued",
            priority,
            prepare_workspace(workspace),
            json_list(constraints),
            json_list(acceptance),
            stamp,
            stamp,
        ),
    )
    job_id = int(cur.lastrowid)
    emit_event(conn, "workflow_selected", ATOMIC_WORKFLOW, job_id=job_id)
    emit_event(conn, "job_submitted", "single capability job submitted", job_id=job_id)
    refresh_job_status(conn, job_id)
    return job_id


def ensure_no_active_step(conn: sqlite3.Connection, job_id: int) -> None:
    active = conn.execute(
        "SELECT id, role, status FROM tasks WHERE job_id = ? AND status IN ('queued', 'running') ORDER BY id",
        (job_id,),
    ).fetchone()
    if active:
        raise SystemExit(
            f"ERROR: job already has active step T{active['id']} {active['role']} {active['status']}; "
            "finish or cancel it before running another capability"
        )


def run_capability(args: argparse.Namespace) -> int:
    required_artifacts = normalize_required_artifacts([*(args.required_artifact or []), *(args.artifact or [])])
    token_check = route_cache.RouteTokenCheck(False, "route token not provided")
    token_workspace = args.workspace
    if args.route_token:
        if args.job_id is not None:
            with closing(connect(args.db)) as token_conn:
                token_workspace = load_job(token_conn, int(args.job_id))["workspace"]
        else:
            token_workspace = prepare_workspace(args.workspace)
        token_check = route_cache.validate_route_token(
            args.route_token,
            role=args.role,
            title=args.title,
            prompt=args.prompt,
            artifacts=required_artifacts,
            workspace=token_workspace,
            goal=args.goal or args.title,
        )
    require_valid_task_input(
        args.role,
        args.title,
        args.prompt,
        required_artifacts,
        skip_llm_guard=token_check.accepted,
    )

    with closing(connect(args.db)) as conn:
        with conn:
            if args.job_id is None:
                job_id = create_capability_job(
                    conn,
                    args.goal or args.title,
                    args.workspace,
                    args.priority,
                    args.constraint,
                    args.acceptance,
                )
            else:
                job_id = int(args.job_id)
                load_job(conn, job_id)
            ensure_no_active_step(conn, job_id)
            task_id = insert_task(
                conn,
                job_id,
                args.role,
                args.title,
                args.prompt,
                [],
                required_artifacts=required_artifacts,
            )
            refresh_job_status(conn, job_id)

    result = execute_task(args.db, job_id, task_id, args.mode, args.timeout)
    with closing(connect(args.db)) as conn:
        audit_result = audit_payload(conn, job_id)

    payload = {
        "job_id": job_id,
        "task_id": task_id,
        "exit_code": result["exit_code"],
        "status": result["status"],
        "log_path": result["log_path"],
        "audit_complete": audit_result["complete"],
        "missing_artifact_kinds": audit_result["missing_artifact_kinds"],
        "missing_artifact_files": audit_result["missing_artifact_files"],
        "summary": result["summary"],
        "route_token": token_check.reason,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"CAPABILITY job={job_id} task={task_id} status={result['status']} exit={result['exit_code']}")
        if result["log_path"]:
            print(f"LOG: {result['log_path']}")
        if audit_result["complete"]:
            print("AUDIT: PASS")
        else:
            print("AUDIT: NOT COMPLETE")
            if audit_result["missing_artifact_kinds"]:
                print("Missing artifact kinds: " + ", ".join(audit_result["missing_artifact_kinds"]))
            if audit_result["missing_artifact_files"]:
                print("Missing artifact files: " + ", ".join(item["path"] for item in audit_result["missing_artifact_files"]))
        if result["summary"]:
            print(result["summary"])
    return int(result["exit_code"] or 0)


def tasks_for_job(conn: sqlite3.Connection, job_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tasks WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()


def latest_runs_for_tasks(conn: sqlite3.Connection, task_ids: Iterable[int]) -> dict[int, sqlite3.Row]:
    ids = [int(item) for item in task_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT runs.*
        FROM runs
        JOIN (
            SELECT task_id, MAX(id) AS max_id
            FROM runs
            WHERE task_id IN ({placeholders})
            GROUP BY task_id
        ) latest ON latest.max_id = runs.id
        ORDER BY runs.id
        """,
        ids,
    ).fetchall()
    return {int(row["task_id"]): row for row in rows}


def latest_events_for_job(conn: sqlite3.Connection, job_id: int, limit: int = 8) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM events WHERE job_id = ? ORDER BY id DESC LIMIT ?",
        (job_id, limit),
    ).fetchall()


def enrich_tasks(conn: sqlite3.Connection, tasks: list[sqlite3.Row], stale_after: int = DEFAULT_STALE_AFTER_SECONDS) -> list[dict[str, Any]]:
    runs = latest_runs_for_tasks(conn, [int(task["id"]) for task in tasks])
    current = datetime.now(timezone.utc)
    enriched: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, 1):
        item = row_to_dict(task)
        item["phase"] = index
        item["phase_total"] = len(tasks)
        deps = [int(dep) for dep in parse_json_list(task["depends_on"])]
        item["depends_on_list"] = deps
        item["required_artifacts_list"] = parse_json_list(task["required_artifacts_json"])
        item["blocked_by"] = [
            dep_id
            for dep_id, status_value in dependency_statuses(conn, deps).items()
            if status_value != "done"
        ]
        task_age = age_seconds(task["updated_at"], current)
        item["age_seconds"] = task_age
        item["age"] = format_duration(task_age)
        item["stale"] = task["status"] == "running" and task_age is not None and task_age >= stale_after
        run = runs.get(int(task["id"]))
        item["latest_run"] = row_to_dict(run) if run else None
        if run:
            run_age = age_seconds(run["started_at"], current)
            item["latest_run"]["age_seconds"] = run_age
            item["latest_run"]["age"] = format_duration(run_age)
        enriched.append(item)
    return enriched


def job_progress(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(tasks)
    done = sum(1 for task in tasks if task["status"] == "done")
    running = [task for task in tasks if task["status"] == "running"]
    failed = [task for task in tasks if str(task["status"]).startswith("failed")]
    queued_ready = [
        task for task in tasks
        if task["status"] == "queued" and not task["blocked_by"]
    ]
    next_task = running[0] if running else (queued_ready[0] if queued_ready else (failed[0] if failed else None))
    return {
        "done": done,
        "total": total,
        "percent": round((done / total) * 100, 1) if total else 0,
        "running": [task["id"] for task in running],
        "failed": [task["id"] for task in failed],
        "ready": [task["id"] for task in queued_ready],
        "current_task": next_task,
    }


def status(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        if args.job_id is not None:
            load_job(conn, args.job_id)
            refresh_job_status(conn, args.job_id)
            job = load_job(conn, args.job_id)
            tasks = tasks_for_job(conn, args.job_id)
            enriched = enrich_tasks(conn, tasks)
            events = latest_events_for_job(conn, args.job_id, args.events)
            payload = {
                "job": row_to_dict(job),
                "workflow": job_workflow(conn, args.job_id),
                "progress": job_progress(enriched),
                "tasks": enriched,
                "events": [row_to_dict(row) for row in events],
            }
        else:
            jobs = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
            payload = {"jobs": [row_to_dict(row) for row in jobs]}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.job_id is None:
        for job in payload["jobs"]:
            print(f"#{job['id']} {job['status']} {job['priority']} {job['goal'][:80]}")
        return

    job = payload["job"]
    print(f"Job #{job['id']}: {job['status']} [{job['priority']}]")
    print(f"Execution mode: {payload['workflow']}")
    progress = payload["progress"]
    current = progress["current_task"]
    current_text = f"T{current['id']} {current['role']} {current['status']}" if current else "-"
    print(f"Progress: {progress['done']}/{progress['total']} ({progress['percent']}%) current={current_text}")
    print(f"Workspace: {job['workspace']}")
    print(f"Goal: {job['goal']}")
    for task in payload["tasks"]:
        deps = ",".join(str(item) for item in task["depends_on_list"]) or "-"
        blocked = ",".join(str(item) for item in task["blocked_by"]) or "-"
        stale = " STALE" if task["stale"] else ""
        run = task["latest_run"]
        run_text = ""
        if run:
            code = "running" if run["exit_code"] is None else f"exit={run['exit_code']}"
            log = f" log={run['log_path']}" if run["log_path"] else ""
            run_text = f" run={code}/{run['age']}{log}"
        print(
            f"  {task['phase']:02d}/{task['phase_total']:02d} "
            f"T{task['id']} {task['status']:<16} {task['role']:<12} "
            f"age={task['age']:<7} deps={deps} blocked_by={blocked}{stale}{run_text} {task['title']}"
        )
    if payload["events"]:
        print("Events:")
        for event in payload["events"]:
            print(f"  {event['created_at']} {event['type']} T{event['task_id'] or '-'} {event['message'][:160]}")


def dependency_statuses(conn: sqlite3.Connection, dep_ids: list[int]) -> dict[int, str]:
    if not dep_ids:
        return {}
    placeholders = ",".join("?" for _ in dep_ids)
    rows = conn.execute(f"SELECT id, status FROM tasks WHERE id IN ({placeholders})", dep_ids).fetchall()
    return {int(row["id"]): row["status"] for row in rows}


def ready_task(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    tasks = ready_tasks(conn, job_id)
    return tasks[0] if tasks else None


def ready_tasks(conn: sqlite3.Connection, job_id: int) -> list[sqlite3.Row]:
    ready: list[sqlite3.Row] = []
    for task in conn.execute(
        "SELECT * FROM tasks WHERE job_id = ? AND status = 'queued' ORDER BY id",
        (job_id,),
    ).fetchall():
        dep_ids = [int(item) for item in parse_json_list(task["depends_on"])]
        statuses = dependency_statuses(conn, dep_ids)
        if len(statuses) != len(dep_ids):
            continue
        if all(status == "done" for status in statuses.values()):
            ready.append(task)
    return ready


def required_artifact_kinds_for_task(
    workflow: str,
    role: str,
    required_artifacts_json: str | None = None,
) -> tuple[str, ...]:
    _ = workflow, role
    if required_artifacts_json:
        task_required = tuple(artifact_kinds(parse_json_list(required_artifacts_json)))
        if task_required:
            return task_required
    return ()


def task_artifact_issues(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    workflow: str,
    task_id: int,
    role: str,
) -> list[str]:
    task = load_task(conn, task_id)
    _ = workflow, role
    specs = artifact_specs_from_json(task["required_artifacts_json"])
    if not specs:
        return []
    artifacts = conn.execute("SELECT * FROM artifacts WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
    by_kind: dict[str, list[sqlite3.Row]] = {}
    for artifact in artifacts:
        by_kind.setdefault(artifact["kind"], []).append(artifact)
    issues: list[str] = []
    for kind, expected_path in specs:
        rows = by_kind.get(kind, [])
        if not rows:
            issues.append(f"missing artifact kind: {kind}")
            continue
        if expected_path:
            matching = [row for row in rows if str(row["path"]).replace("\\", "/") == expected_path.replace("\\", "/")]
            if not matching:
                issues.append(f"missing artifact path for kind {kind}: {expected_path}")
                continue
            if not any(resolve_artifact_path(job["workspace"], row["path"]).exists() for row in matching):
                issues.append(f"missing artifact file for kind {kind}: {expected_path}")
            continue
        if not any(resolve_artifact_path(job["workspace"], row["path"]).exists() for row in rows):
            issues.append(f"missing artifact file for kind: {kind}")
    return issues


def auto_record_expected_artifacts(conn: sqlite3.Connection, job: sqlite3.Row, task: sqlite3.Row) -> None:
    for kind, expected_path in artifact_specs_from_json(task["required_artifacts_json"]):
        if not expected_path:
            continue
        if not resolve_artifact_path(job["workspace"], expected_path).exists():
            continue
        exists = conn.execute(
            """
            SELECT 1
            FROM artifacts
            WHERE task_id = ? AND kind = ? AND replace(path, '\\', '/') = ?
            LIMIT 1
            """,
            (int(task["id"]), kind, expected_path.replace("\\", "/")),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO artifacts(task_id, path, kind, summary, created_at) VALUES (?, ?, ?, ?, ?)",
            (int(task["id"]), expected_path, kind, f"expected {kind} artifact", now()),
        )
        emit_event(
            conn,
            "artifact_recorded",
            f"{kind}: {expected_path}",
            job_id=int(job["id"]),
            task_id=int(task["id"]),
        )


def strip_task_footers(prompt: str) -> str:
    text = str(prompt or "").strip()
    marker_positions = [
        position
        for marker in (
            ROLE_BOUNDARY_MARKER,
            FRONTEND_DESIGN_MARKER,
            REQUIRED_ARTIFACT_MARKER,
            EXPERIENCE_FOOTER_MARKER,
        )
        if (position := text.find(marker)) >= 0
    ]
    if marker_positions:
        text = text[: min(marker_positions)].rstrip()
    return text


def artifact_path_suffix(path: str) -> str:
    name = str(path or "").replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    return name[dot:].lower()


def assetgen_command_paths(required_artifacts_json: str | None) -> tuple[list[str], str]:
    outputs: list[str] = []
    manifest = ""
    for kind, path in artifact_specs_from_json(required_artifacts_json):
        lowered_kind = kind.lower()
        suffix = artifact_path_suffix(path)
        if lowered_kind == "local_asset_manifest" and path:
            manifest = path
            continue
        if lowered_kind in ASSETGEN_IMAGE_KINDS and path and suffix in ASSETGEN_RASTER_EXTENSIONS:
            outputs.append(path)
    return outputs, manifest


def infer_asset_role(title: str, prompt: str) -> str:
    folded = f"{title}\n{prompt}".lower()
    if any(word in folded for word in ("game", "sprite", "texture", "tile", "prop")):
        return "game"
    if any(word in folded for word in ("video", "thumbnail", "key art", "storyboard", "overlay")):
        return "video"
    if any(word in folded for word in ("web", "website", "hero", "banner", "landing", "app")):
        return "web"
    return "other"


def build_worker_command(
    job: sqlite3.Row,
    workflow: str,
    task: sqlite3.Row,
    mode: str,
) -> tuple[list[str], dict[str, Any]]:
    if task["role"] == "assetgen":
        outputs, manifest = assetgen_command_paths(task["required_artifacts_json"])
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "assetgen_exec.py"),
            "--workspace",
            job["workspace"],
            "--prompt",
            strip_task_footers(task["prompt"]),
            "--asset-role",
            infer_asset_role(task["title"], task["prompt"]),
            "--prompt-template-top",
            os.environ.get("ASSETGEN_PROMPT_TEMPLATE_TOP", "3"),
        ]
        for output_path in outputs:
            cmd.extend(["--output", output_path])
        if manifest:
            cmd.extend(["--manifest", manifest])
    else:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "codex_exec.py"),
            "-w",
            job["workspace"],
            "-m",
            mode,
        ]
    policy_payload: dict[str, Any] = {"enabled": False}
    if model_policy.policy_enabled():
        choice = model_policy.select_model(workflow, task["role"])
        resolved = model_policy.apply_env_overrides(choice, os.environ)
        policy_payload = {
            "enabled": True,
            "workflow": workflow,
            "role": task["role"],
            **resolved,
        }
        if not resolved["model_overridden_by_env"]:
            cmd.extend(["--model", choice.model])
        if not resolved["reasoning_overridden_by_env"]:
            cmd.extend(["--reasoning-effort", choice.reasoning_effort])
    if task["role"] == "assetgen":
        pass
    else:
        cmd.append(task["prompt"])
    return cmd, policy_payload


def task_plan_payload(
    job: sqlite3.Row,
    workflow: str,
    task: sqlite3.Row,
    mode: str,
) -> dict[str, Any]:
    cmd, policy_payload = build_worker_command(job, workflow, task, mode)
    return {
        "task_id": task["id"],
        "role": task["role"],
        "title": task["title"],
        "workflow": workflow,
        "required_artifacts": parse_json_list(task["required_artifacts_json"]),
        "model_policy": policy_payload,
        "command": cmd,
    }


def execute_task(
    db: str | None,
    job_id: int,
    task_id: int,
    mode: str,
    timeout: int,
) -> dict[str, Any]:
    with closing(connect(db)) as conn:
        job = load_job(conn, job_id)
        workflow = job_workflow(conn, job_id)
        task = load_task(conn, task_id)
        if task["status"] != "queued":
            return {
                "task_id": task_id,
                "role": task["role"],
                "title": task["title"],
                "exit_code": 1,
                "status": task["status"],
                "summary": f"task is not queued: {task['status']}",
                "run_id": None,
                "log_path": "",
            }
        dep_ids = [int(item) for item in parse_json_list(task["depends_on"])]
        dep_statuses = dependency_statuses(conn, dep_ids)
        if len(dep_statuses) != len(dep_ids) or any(status != "done" for status in dep_statuses.values()):
            return {
                "task_id": task_id,
                "role": task["role"],
                "title": task["title"],
                "exit_code": 1,
                "status": "blocked",
                "summary": "dependencies are not all done",
                "run_id": None,
                "log_path": "",
            }
        cmd, policy_payload = build_worker_command(job, workflow, task, mode)
        started = now()
        with conn:
            cur = conn.execute(
                "UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ? AND status = 'queued'",
                (started, task_id),
            )
            if cur.rowcount != 1:
                refreshed = load_task(conn, task_id)
                return {
                    "task_id": task_id,
                    "role": refreshed["role"],
                    "title": refreshed["title"],
                    "exit_code": 1,
                    "status": refreshed["status"],
                    "summary": f"task changed before start: {refreshed['status']}",
                    "run_id": None,
                    "log_path": "",
                }
            run_cur = conn.execute(
                """
                INSERT INTO runs(task_id, command, log_path, exit_code, stdout_summary, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, json.dumps(cmd), "", None, "running", started, ""),
            )
            run_id = int(run_cur.lastrowid)
            refresh_job_status(conn, job_id)
            suffix = ""
            if policy_payload.get("enabled"):
                suffix = f" [{policy_payload['model']}/{policy_payload['reasoning_effort']}]"
            emit_event(conn, "task_started", f"{task['role']}: {task['title']}{suffix} run={run_id}", job_id=job_id, task_id=task_id)

    env = os.environ.copy()
    env["TASKCTL_DB"] = str(db_path(db))
    env["TASKCTL_JOB_ID"] = str(job_id)
    env["TASKCTL_TASK_ID"] = str(task_id)
    worker_result = worker_runner.run_worker_command(
        cmd,
        env=env,
        workspace=job["workspace"],
        timeout=timeout,
    )
    output = worker_result.output
    return_code = worker_result.return_code
    finished = now()
    log_path = worker_result.log_path
    if return_code == 0:
        with closing(connect(db)) as validation_conn:
            with validation_conn:
                validation_job = load_job(validation_conn, job_id)
                validation_task = load_task(validation_conn, task_id)
                auto_record_expected_artifacts(validation_conn, validation_job, validation_task)
            validation_job = load_job(validation_conn, job_id)
            artifact_issues = task_artifact_issues(validation_conn, validation_job, workflow, task_id, task["role"])
        if artifact_issues:
            output += "\nARTIFACT VALIDATION FAILED: " + "; ".join(artifact_issues)
            return_code = 3
    summary = "\n".join(line for line in output.splitlines()[-12:])
    new_status = "done" if return_code == 0 else "failed_retryable"

    with closing(connect(db)) as conn:
        with conn:
            conn.execute(
                """
                UPDATE runs
                SET log_path = ?, exit_code = ?, stdout_summary = ?, finished_at = ?
                WHERE id = ?
                """,
                (log_path, return_code, summary, finished, run_id),
            )
            conn.execute(
                "UPDATE tasks SET status = ?, result_summary = ?, updated_at = ? WHERE id = ?",
                (new_status, summary, finished, task_id),
            )
            emit_event(
                conn,
                "task_finished" if return_code == 0 else "task_failed",
                f"run={run_id} exit={return_code} {summary[:460]}",
                job_id=job_id,
                task_id=task_id,
            )
            refresh_job_status(conn, job_id)

    return {
        "task_id": task_id,
        "role": task["role"],
        "title": task["title"],
        "exit_code": return_code,
        "status": new_status,
        "summary": summary,
        "run_id": run_id,
        "log_path": log_path,
    }


def run_next(args: argparse.Namespace) -> int:
    with closing(connect(args.db)) as conn:
        job = load_job(conn, args.job_id)
        workflow = job_workflow(conn, args.job_id)
        task = ready_task(conn, args.job_id)
        if task is None:
            refresh_job_status(conn, args.job_id)
            print(f"No ready task for job #{args.job_id}")
            return 1

        if args.dry_run:
            print(
                json.dumps(
                    task_plan_payload(job, workflow, task, args.mode),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

    result = execute_task(args.db, args.job_id, int(task["id"]), args.mode, args.timeout)
    print(result["summary"])
    return int(result["exit_code"] or 0)




def complete_task(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        task = load_task(conn, args.task_id)
        stamp = now()
        with conn:
            conn.execute(
                "UPDATE tasks SET status = 'done', result_summary = ?, updated_at = ? WHERE id = ?",
                (args.summary, stamp, args.task_id),
            )
            conn.execute(
                """
                UPDATE runs
                SET exit_code = 0,
                    stdout_summary = ?,
                    finished_at = ?
                WHERE task_id = ?
                  AND exit_code IS NULL
                  AND finished_at = ''
                """,
                (f"manual completion: {args.summary}", stamp, args.task_id),
            )
            emit_event(conn, "task_completed", args.summary, job_id=task["job_id"], task_id=args.task_id)
            refresh_job_status(conn, task["job_id"])
    print(f"TASK {args.task_id} done")


def fail_task(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        task = load_task(conn, args.task_id)
        new_status = "failed_retryable" if args.retryable else "failed_terminal"
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, result_summary = ?, updated_at = ? WHERE id = ?",
                (new_status, args.summary, now(), args.task_id),
            )
            emit_event(conn, "task_failed", args.summary, job_id=task["job_id"], task_id=args.task_id)
            refresh_job_status(conn, task["job_id"])
    print(f"TASK {args.task_id} {new_status}")


def retry_task(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        task = load_task(conn, args.task_id)
        if task["status"] == "done" and not args.force:
            raise SystemExit("ERROR: cannot retry a done task")
        if task["status"] == "running" and not args.force:
            task_age = age_seconds(task["updated_at"])
            if task_age is not None and task_age < args.stale_after:
                raise SystemExit(
                    f"ERROR: task is still running age={format_duration(task_age)}; "
                    f"use --force or wait until stale_after={format_duration(args.stale_after)}"
                )
        if task["status"] not in {"failed_retryable", "failed_terminal", "blocked", "running"} and not args.force:
            raise SystemExit(f"ERROR: task status is {task['status']}; use --force to requeue it")
        stamp = now()
        summary = args.summary or f"requeued from {task['status']}"
        with conn:
            conn.execute(
                "UPDATE tasks SET status = 'queued', result_summary = ?, updated_at = ? WHERE id = ?",
                (summary, stamp, args.task_id),
            )
            emit_event(
                conn,
                "task_requeued",
                summary,
                job_id=task["job_id"],
                task_id=args.task_id,
            )
            refresh_job_status(conn, task["job_id"])
    print(f"TASK {args.task_id} queued")


def cancel_job(args: argparse.Namespace) -> None:
    summary = compact_text(args.summary or "job canceled", 500)
    stamp = now()
    with closing(connect(args.db)) as conn:
        load_job(conn, args.job_id)
        with conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'canceled', result_summary = ?, updated_at = ?
                WHERE job_id = ? AND status != 'done'
                """,
                (summary, stamp, args.job_id),
            )
            conn.execute(
                "UPDATE jobs SET status = 'canceled', updated_at = ? WHERE id = ?",
                (stamp, args.job_id),
            )
            conn.execute(
                """
                UPDATE runs
                SET exit_code = 130,
                    stdout_summary = ?,
                    finished_at = ?
                WHERE exit_code IS NULL
                  AND task_id IN (SELECT id FROM tasks WHERE job_id = ?)
                """,
                (f"canceled: {summary}", stamp, args.job_id),
            )
            emit_event(conn, "job_canceled", summary, job_id=args.job_id)
    print(f"JOB {args.job_id} canceled")




def enqueue(args: argparse.Namespace) -> None:
    required_artifacts = normalize_required_artifacts(args.required_artifact)
    require_valid_task_input(args.role, args.title, args.prompt, required_artifacts)
    with closing(connect(args.db)) as conn:
        load_job(conn, args.job_id)
        workflow = job_workflow(conn, args.job_id)
        if workflow == ATOMIC_WORKFLOW:
            ensure_no_active_step(conn, args.job_id)
        deps: list[int] = []
        with conn:
            task_id = insert_task(
                conn,
                args.job_id,
                args.role,
                args.title,
                args.prompt,
                deps,
                required_artifacts=required_artifacts,
            )
            refresh_job_status(conn, args.job_id)
    print(f"TASK {task_id} queued")


def filter_input(args: argparse.Namespace) -> int:
    required_artifacts = normalize_required_artifacts(args.required_artifact)
    result = validate_task_input(args.role, args.title, args.prompt, required_artifacts)
    payload = result.__dict__
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("PASS" if result.passed else "FAIL")
        for violation in result.violations:
            print(f"  violation: {violation}")
        for warning in result.warnings:
            print(f"  warning: {warning}")
        print(f"Risk score: {result.risk_score}/100")
        print(f"Convergence: {result.convergence_score:.2f}")
    return 0 if result.passed else 1


def add_review(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        load_job(conn, args.job_id)
        issues = json.loads(args.issues_json)
        tests = json.loads(args.tests_json)
        with conn:
            conn.execute(
                "INSERT INTO reviews(job_id, verdict, issues_json, tests_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (args.job_id, args.verdict, json.dumps(issues, ensure_ascii=False), json.dumps(tests, ensure_ascii=False), now()),
            )
            emit_event(conn, "review_added", args.verdict, job_id=args.job_id)
    print(f"REVIEW {args.verdict}")


def add_artifact(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        task = load_task(conn, args.task_id)
        artifact_path = args.path.strip()
        if not artifact_path:
            raise SystemExit("ERROR: --path is required")
        with conn:
            conn.execute(
                "INSERT INTO artifacts(task_id, path, kind, summary, created_at) VALUES (?, ?, ?, ?, ?)",
                (args.task_id, artifact_path, args.kind, args.summary or "", now()),
            )
            emit_event(
                conn,
                "artifact_recorded",
                f"{args.kind}: {artifact_path}",
                job_id=task["job_id"],
                task_id=args.task_id,
            )
    print(f"ARTIFACT {args.kind} {artifact_path}")


def load_experience(conn: sqlite3.Connection, experience_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM experiences WHERE id = ?", (experience_id,)).fetchone()
    if row is None:
        raise SystemExit(f"ERROR: experience not found: {experience_id}")
    return row


def add_experience(args: argparse.Namespace) -> None:
    if args.kind not in EXPERIENCE_KINDS:
        raise SystemExit(f"ERROR: unsupported experience kind: {args.kind}")
    status = "candidate"
    confidence = int(args.confidence)
    if confidence < 1 or confidence > 5:
        raise SystemExit("ERROR: --confidence must be between 1 and 5")

    with closing(connect(args.db)) as conn:
        task = load_task(conn, args.task_id)
        load_job(conn, task["job_id"])
        workflow = job_workflow(conn, task["job_id"])
        title = compact_text(args.title, 160)
        summary = compact_text(args.summary, 700)
        if not title or not summary:
            raise SystemExit("ERROR: --title and --summary are required")
        evidence = compact_text(args.evidence or "", 500)
        reuse_hint = compact_text(args.reuse or "", 500)
        source_path = compact_text(args.source_path or "", 260)
        tags = normalize_tags(args.tag)
        stamp = now()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO experiences(
                    job_id, task_id, workflow, role, kind, title, summary,
                    evidence, reuse_hint, tags_json, confidence, status,
                    source_path, supersedes_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(task["job_id"]),
                    args.task_id,
                    workflow,
                    task["role"],
                    args.kind,
                    title,
                    summary,
                    evidence,
                    reuse_hint,
                    json.dumps(tags, ensure_ascii=False),
                    confidence,
                    status,
                    source_path,
                    args.supersedes,
                    stamp,
                    stamp,
                ),
            )
            experience_id = int(cur.lastrowid)
            emit_event(
                conn,
                "experience_added",
                f"{status}:{args.kind}: {title}",
                job_id=int(task["job_id"]),
                task_id=args.task_id,
            )
    print(f"EXPERIENCE {experience_id} {status} {args.kind}")


def experience_query(args: argparse.Namespace) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    statuses = args.status or ["accepted"]
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        values.extend(statuses)
    if args.job_id is not None:
        clauses.append("job_id = ?")
        values.append(args.job_id)
    if args.task_id is not None:
        clauses.append("task_id = ?")
        values.append(args.task_id)
    if args.workflow:
        clauses.append("workflow = ?")
        values.append(args.workflow)
    if args.role:
        clauses.append("role = ?")
        values.append(args.role)
    if args.kind:
        clauses.append("kind = ?")
        values.append(args.kind)
    if args.tag:
        for tag in normalize_tags(args.tag):
            clauses.append("tags_json LIKE ?")
            values.append(f"%{tag}%")
    if args.query:
        needle = f"%{args.query.lower()}%"
        clauses.append(
            "(lower(title) LIKE ? OR lower(summary) LIKE ? OR lower(reuse_hint) LIKE ? OR lower(tags_json) LIKE ?)"
        )
        values.extend([needle, needle, needle, needle])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, values


def experience_rows(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    where, values = experience_query(args)
    limit = max(1, min(int(args.limit), 500))
    return conn.execute(
        f"""
        SELECT *
        FROM experiences
        {where}
        ORDER BY
          CASE status WHEN 'accepted' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END,
          confidence DESC,
          id DESC
        LIMIT ?
        """,
        [*values, limit],
    ).fetchall()


def list_experiences(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        rows = experience_rows(conn, args)
    payload = [row_to_dict(row) | {"tags": parse_json_list(row["tags_json"])} for row in rows]
    if args.json:
        print(json.dumps({"experiences": payload}, ensure_ascii=False, indent=2))
        return
    if not payload:
        print("No experiences found")
        return
    for item in payload:
        tags = ",".join(item["tags"]) or "-"
        print(
            f"E{item['id']} {item['status']:<10} {item['kind']:<12} "
            f"{item['workflow']}/{item['role']} c={item['confidence']} tags={tags} {item['title']}"
        )
        if item["reuse_hint"]:
            print(f"  reuse: {item['reuse_hint']}")


def accept_experience(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        row = load_experience(conn, args.experience_id)
        tags = normalize_tags([*parse_json_list(row["tags_json"]), *(args.tag or [])])
        confidence = int(args.confidence or row["confidence"])
        if confidence < 1 or confidence > 5:
            raise SystemExit("ERROR: --confidence must be between 1 and 5")
        stamp = now()
        with conn:
            conn.execute(
                """
                UPDATE experiences
                SET status = 'accepted', confidence = ?, tags_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (confidence, json.dumps(tags, ensure_ascii=False), stamp, args.experience_id),
            )
            for old_id in args.supersedes or []:
                conn.execute(
                    "UPDATE experiences SET status = 'superseded', supersedes_id = ?, updated_at = ? WHERE id = ?",
                    (args.experience_id, stamp, int(old_id)),
                )
            emit_event(
                conn,
                "experience_accepted",
                row["title"],
                job_id=row["job_id"],
                task_id=row["task_id"],
            )
    print(f"EXPERIENCE {args.experience_id} accepted")


def reject_experience(args: argparse.Namespace) -> None:
    reason = compact_text(args.reason, 500)
    with closing(connect(args.db)) as conn:
        row = load_experience(conn, args.experience_id)
        stamp = now()
        with conn:
            summary = row["summary"]
            if reason:
                summary = compact_text(f"{summary} Rejected reason: {reason}", 900)
            conn.execute(
                "UPDATE experiences SET status = 'rejected', summary = ?, updated_at = ? WHERE id = ?",
                (summary, stamp, args.experience_id),
            )
            emit_event(
                conn,
                "experience_rejected",
                reason or row["title"],
                job_id=row["job_id"],
                task_id=row["task_id"],
            )
    print(f"EXPERIENCE {args.experience_id} rejected")


def prune_experiences(args: argparse.Namespace) -> None:
    statuses = args.status or ["rejected", "superseded", "stale"]
    bad_statuses = [status for status in statuses if status not in EXPERIENCE_STATUSES]
    if bad_statuses:
        raise SystemExit(f"ERROR: unsupported status: {', '.join(bad_statuses)}")
    placeholders = ",".join("?" for _ in statuses)
    with closing(connect(args.db)) as conn:
        with conn:
            cur = conn.execute(f"DELETE FROM experiences WHERE status IN ({placeholders})", statuses)
    print(f"PRUNED {cur.rowcount} experiences")


def default_experience_skill_dirs() -> list[Path]:
    primary = CLAUDE_DIR / "skills" / "learned-experience"
    plugin = CLAUDE_DIR / "plugins" / "task-decompose" / "skills" / "learned-experience"
    dirs = [primary]
    if (CLAUDE_DIR / "plugins" / "task-decompose").exists():
        dirs.append(plugin)
    return dirs


def render_experience_skill(rows: list[sqlite3.Row]) -> tuple[str, str]:
    skill_md = """---
name: learned-experience
description: Search and apply compact lessons from completed taskctl/Codex worker runs. Use when starting, planning, implementing, testing, reviewing, closing, or repairing single-step task-control and Codex worker capabilities.
---

# Learned Experience

Use this skill as a retrieval entrypoint, not as a rulebook.

1. Search SQLite first:

```bash
{script_command('taskctl.py')} experience-list --query "<term>" --status accepted --json
```

2. If SQLite is unavailable or you need a compact offline index, read
   `references/experience-index.md`.
3. Treat lessons as evidence-backed hints. Current repository code, task
   artifacts, tests, and official docs override old lessons.
4. If a lesson is wrong, stale, duplicate, or too broad, reject or supersede it
   through `taskctl.py` and rerun `experience-sync-skill`.
"""

    lines = [
        "# Experience Index",
        "",
        "Generated from accepted SQLite experience records.",
        "Search first with `taskctl.py experience-list --query \"<term>\" --status accepted`.",
        "",
    ]
    if not rows:
        lines.extend(["No accepted lessons yet.", ""])
    current_group = ""
    for row in rows:
        tags = ", ".join(parse_json_list(row["tags_json"])) or "-"
        group = f"{row['workflow']} / {row['role']}"
        if group != current_group:
            current_group = group
            lines.extend([f"## {group}", ""])
        lines.extend(
            [
                f"### E{row['id']}: {row['title']}",
                f"- Kind: {row['kind']}",
                f"- Confidence: {row['confidence']}/5",
                f"- Tags: {tags}",
                f"- Summary: {row['summary']}",
            ]
        )
        if row["reuse_hint"]:
            lines.append(f"- Reuse: {row['reuse_hint']}")
        if row["evidence"]:
            lines.append(f"- Evidence: {row['evidence']}")
        if row["source_path"]:
            lines.append(f"- Source: {row['source_path']}")
        lines.append("")
    return skill_md, "\n".join(lines)


def write_experience_skill(skill_dir: Path, rows: list[sqlite3.Row]) -> None:
    skill_md, index_md = render_experience_skill(rows)
    references_dir = skill_dir / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (references_dir / "experience-index.md").write_text(index_md, encoding="utf-8")


def sync_experience_skill(args: argparse.Namespace) -> None:
    list_args = argparse.Namespace(
        status=["accepted"],
        job_id=None,
        task_id=None,
        workflow=args.workflow,
        role=None,
        kind=None,
        tag=None,
        query=None,
        limit=args.limit,
    )
    with closing(connect(args.db)) as conn:
        rows = experience_rows(conn, list_args)

    if args.skill_dir:
        targets = [Path(args.skill_dir).expanduser().resolve()]
    else:
        targets = default_experience_skill_dirs()
        if args.no_plugin_mirror:
            targets = targets[:1]

    if args.dry_run:
        print(json.dumps({"targets": [str(path) for path in targets], "accepted": len(rows)}, ensure_ascii=False, indent=2))
        return

    for target in targets:
        write_experience_skill(target, rows)
    print(f"SYNCED learned-experience skill ({len(rows)} lessons) -> " + ", ".join(str(path) for path in targets))


def job_workflow(conn: sqlite3.Connection, job_id: int) -> str:
    row = conn.execute(
        "SELECT message FROM events WHERE job_id = ? AND type = 'workflow_selected' ORDER BY id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    return row["message"] if row else "general"


def artifacts_for_job(conn: sqlite3.Connection, job_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT artifacts.*
        FROM artifacts
        JOIN tasks ON tasks.id = artifacts.task_id
        WHERE tasks.job_id = ?
        ORDER BY artifacts.id
        """,
        (job_id,),
    ).fetchall()


def resolve_artifact_path(workspace: str, artifact_path: str) -> Path:
    path = Path(artifact_path)
    if path.is_absolute():
        return path
    return Path(workspace) / path


def artifact_spec_string(kind: str, path: str) -> str:
    return f"{kind}:{path}" if path else kind


def task_required_artifact_strings(task: sqlite3.Row | dict[str, Any]) -> list[str]:
    return [
        artifact_spec_string(kind, path)
        for kind, path in artifact_specs_from_json(str(task["required_artifacts_json"] or "[]"))
    ]


def audit_resume_fields(
    job: sqlite3.Row,
    tasks: list[sqlite3.Row],
    complete: bool,
) -> dict[str, Any]:
    if complete:
        return {"next_role": "", "next_artifacts": [], "next_command": "", "resume_hint": ""}

    unfinished = [task for task in tasks if task["status"] != "done"]
    candidate = unfinished[0] if unfinished else (tasks[-1] if tasks else None)
    if candidate is None:
        return {
            "next_role": "",
            "next_artifacts": [],
            "next_command": command_catalog.next_command("capability", job["workspace"]),
            "resume_hint": "Create one bounded capability step for the remaining work.",
        }

    next_role = str(candidate["role"])
    next_artifacts = task_required_artifact_strings(candidate)
    status = str(candidate["status"])
    if status in {"failed", "failed_retryable", "failed_terminal", "blocked"}:
        next_command = f"{script_command('taskctl.py')} retry-task {candidate['id']}"
        resume_hint = (
            f"Retry task {candidate['id']} after inspecting status/audit, or run one bounded "
            f"{next_role} capability to produce: {', '.join(next_artifacts) or 'the missing artifact'}."
        )
    elif status == "queued":
        next_command = f"{script_command('taskctl.py')} run-next {job['id']}"
        resume_hint = f"Run the queued {next_role} task and verify required artifacts."
    else:
        next_command = f"{script_command('taskctl.py')} status {job['id']}"
        resume_hint = f"Inspect task {candidate['id']} ({status}) before deciding the next bounded capability."
    return {
        "next_role": next_role,
        "next_artifacts": next_artifacts,
        "next_command": next_command,
        "resume_hint": resume_hint,
    }


def audit_payload(conn: sqlite3.Connection, job_id: int, *, include_quality: bool = False) -> dict[str, Any]:
    load_job(conn, job_id)
    refresh_job_status(conn, job_id)
    job = load_job(conn, job_id)
    tasks = tasks_for_job(conn, job_id)
    workflow = job_workflow(conn, job_id)
    required_roles = required_roles_for_workflow(workflow)
    roles_present = {task["role"] for task in tasks}
    missing_roles = [role for role in required_roles if role not in roles_present]
    unfinished = [row_to_dict(task) for task in tasks if task["status"] != "done"]
    reviews = conn.execute("SELECT * FROM reviews WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
    artifacts = [row_to_dict(row) for row in artifacts_for_job(conn, job_id)]
    experience_counts = {
        row["status"]: int(row["count"])
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM experiences WHERE job_id = ? GROUP BY status",
            (job_id,),
        ).fetchall()
    }

    required_artifact_kinds = required_artifact_kinds_for_workflow(workflow)

    missing_artifact_kinds: list[str] = []
    missing_artifact_files: list[dict[str, str]] = []
    if required_artifact_kinds:
        present_kinds = {artifact["kind"] for artifact in artifacts}
        missing_artifact_kinds = [
            kind for kind in required_artifact_kinds if kind not in present_kinds
        ]
    for task in tasks:
        task_specs = artifact_specs_from_json(task["required_artifacts_json"])
        if not task_specs:
            continue
        rows = [artifact for artifact in artifacts if int(artifact["task_id"]) == int(task["id"])]
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_kind.setdefault(row["kind"], []).append(row)
        for kind, expected_path in task_specs:
            candidates = by_kind.get(kind, [])
            if not candidates:
                if expected_path:
                    missing_artifact_kinds.append(f"T{task['id']}:{kind}:{expected_path}")
                else:
                    missing_artifact_kinds.append(f"T{task['id']}:{kind}")
                continue
            if expected_path:
                candidates = [
                    row
                    for row in candidates
                    if str(row["path"]).replace("\\", "/") == expected_path.replace("\\", "/")
                ]
                if not candidates:
                    missing_artifact_kinds.append(f"T{task['id']}:{kind}:{expected_path}")
                    continue
            if not any(resolve_artifact_path(job["workspace"], row["path"]).exists() for row in candidates):
                path_value = expected_path or candidates[0]["path"]
                missing_artifact_files.append(
                    {
                        "kind": kind,
                        "path": path_value,
                        "resolved_path": str(resolve_artifact_path(job["workspace"], path_value)),
                    }
                )

    complete = (
        job["status"] == "done"
        and not missing_roles
        and not unfinished
        and not missing_artifact_kinds
        and not missing_artifact_files
    )
    resume = audit_resume_fields(job, tasks, complete)
    payload = {
        "job_id": job_id,
        "job_status": job["status"],
        "workflow": workflow,
        "required_roles": list(required_roles),
        "missing_roles": missing_roles,
        "unfinished_tasks": unfinished,
        "required_artifact_kinds": list(required_artifact_kinds),
        "missing_artifact_kinds": missing_artifact_kinds,
        "missing_artifact_files": missing_artifact_files,
        "artifacts": artifacts,
        "reviews": [row_to_dict(row) for row in reviews],
        "experience_counts": experience_counts,
        **resume,
        "complete": complete,
    }
    if include_quality:
        task_by_id = {int(task["id"]): task for task in tasks}
        quality_artifacts: list[dict[str, Any]] = []
        for artifact in artifacts:
            quality_artifact = dict(artifact)
            task = task_by_id.get(int(artifact["task_id"]))
            if task is not None:
                quality_artifact["role"] = str(task["role"])
            quality_artifact["resolved_path"] = str(resolve_artifact_path(job["workspace"], artifact["path"]))
            quality_artifacts.append(quality_artifact)
        quality_issues = artifact_quality.validate_artifacts(job["workspace"], quality_artifacts)
        payload.update(
            {
                "quality_checked": True,
                "quality_complete": not quality_issues,
                "quality_issues": quality_issues,
            }
        )
    return payload


def print_audit_payload(payload: dict[str, Any]) -> None:
    audit_pass = payload["complete"] and (not payload.get("quality_checked") or payload.get("quality_complete", True))
    missing_roles = payload["missing_roles"]
    unfinished = payload["unfinished_tasks"]
    missing_artifact_kinds = payload["missing_artifact_kinds"]
    missing_artifact_files = payload["missing_artifact_files"]
    reviews = payload["reviews"]
    experience_counts = payload["experience_counts"]
    print(f"Audit job #{payload['job_id']}: {'PASS' if audit_pass else 'NOT COMPLETE'}")
    if missing_roles:
        print("Missing roles: " + ", ".join(missing_roles))
    if unfinished:
        print("Unfinished tasks: " + ", ".join(f"T{task['id']}:{task['status']}" for task in unfinished))
    if missing_artifact_kinds:
        print("Missing artifact kinds: " + ", ".join(missing_artifact_kinds))
    if missing_artifact_files:
        print("Missing artifact files: " + ", ".join(item["path"] for item in missing_artifact_files))
    if payload.get("quality_checked"):
        quality_issues = payload.get("quality_issues") or []
        if quality_issues:
            print("Artifact quality issues:")
            for issue in quality_issues:
                print(
                    "  "
                    f"T{issue.get('task_id')} {issue.get('role')} {issue.get('path')}: "
                    f"missing {', '.join(issue.get('missing') or [])}"
                )
        else:
            print("Artifact quality: pass")
    if not reviews:
        print("Reviews: none recorded")
    if experience_counts:
        print("Experiences: " + ", ".join(f"{key}={value}" for key, value in sorted(experience_counts.items())))
    if not payload["complete"] and payload.get("resume_hint"):
        print("Next role: " + (payload.get("next_role") or "-"))
        next_artifacts = payload.get("next_artifacts") or []
        if next_artifacts:
            print("Next artifacts: " + ", ".join(next_artifacts))
        print("Resume hint: " + payload["resume_hint"])


def audit(args: argparse.Namespace) -> int:
    with closing(connect(args.db)) as conn:
        payload = audit_payload(conn, args.job_id, include_quality=args.quality)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_audit_payload(payload)
    audit_pass = payload["complete"] and (not args.quality or payload.get("quality_complete", True))
    return 0 if audit_pass else 1


def slugify(value: str, fallback: str = "checkpoint") -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", str(value or "").strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text[:80] or fallback


def yaml_string(value: Any) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def latest_run_for_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()


def checkpoint_markdown(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    tasks: list[sqlite3.Row],
    audit_data: dict[str, Any],
    title: str,
    timestamp: str,
) -> str:
    task = next((item for item in tasks if item["status"] != "done"), tasks[-1] if tasks else None)
    last_run = latest_run_for_task(conn, int(task["id"])) if task is not None else None
    completed_artifacts = [
        f"- {artifact['kind']}:{artifact['path']}"
        for artifact in audit_data["artifacts"]
        if not audit_data["missing_artifact_files"]
    ]
    missing_items = [
        *(f"- {item}" for item in audit_data["missing_artifact_kinds"]),
        *(f"- {item['kind']}:{item['path']}" for item in audit_data["missing_artifact_files"]),
    ]
    failed_command = ""
    if last_run is not None and last_run["exit_code"] not in (None, 0):
        failed_command = str(last_run["command"] or "")
    blocker = ""
    if task is not None and task["status"] != "done":
        blocker = str(task["result_summary"] or task["status"])
    frontmatter = [
        "---",
        f"job_id: {job['id']}",
        f"timestamp: {yaml_string(timestamp)}",
        f"title: {yaml_string(title)}",
        f"source_role: {yaml_string(task['role'] if task is not None else '')}",
        f"status: {yaml_string('resolved' if audit_data['complete'] else 'in-progress')}",
        f"next_role: {yaml_string(audit_data.get('next_role', ''))}",
        f"next_command: {yaml_string(audit_data.get('next_command', ''))}",
        "---",
        "",
    ]
    sections = [
        "# Taskctl Checkpoint",
        "",
        "## User Goal",
        str(job["goal"] or ""),
        "",
        "## Current State",
        f"- Job: {job['id']} ({job['status']})",
        f"- Audit complete: {audit_data['complete']}",
        f"- Source role: {task['role'] if task is not None else '-'}",
        "",
        "## Completed Artifacts",
        "\n".join(completed_artifacts) if completed_artifacts else "(none recorded)",
        "",
        "## Missing Artifacts",
        "\n".join(missing_items) if missing_items else "(none)",
        "",
        "## Last Failed Command",
        failed_command or "(none recorded)",
        "",
        "## Known Blocker",
        blocker or "(none recorded)",
        "",
        "## Recommended Next Step",
        str(audit_data.get("resume_hint") or "(none)"),
        "",
    ]
    return "\n".join([*frontmatter, *sections])


def save_checkpoint(args: argparse.Namespace) -> int:
    with closing(connect(args.db)) as conn:
        job = load_job(conn, args.job_id)
        tasks = tasks_for_job(conn, args.job_id)
        audit_data = audit_payload(conn, args.job_id)
        timestamp = now()
        title = args.title.strip() if args.title else f"job-{args.job_id}"
        directory = checkpoint_dir(job["workspace"])
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{timestamp.replace(':', '').replace('-', '')}-job-{args.job_id}-{slugify(title)}.md"
        path = directory / filename
        path.write_text(checkpoint_markdown(conn, job, tasks, audit_data, title, timestamp), encoding="utf-8")
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO checkpoints(job_id, title, path, status, next_role, next_command, resume_hint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    args.job_id,
                    title,
                    str(path),
                    "resolved" if audit_data["complete"] else "in-progress",
                    audit_data.get("next_role", ""),
                    audit_data.get("next_command", ""),
                    audit_data.get("resume_hint", ""),
                    timestamp,
                ),
            )
            checkpoint_id = int(cursor.lastrowid)
    payload = {
        "id": checkpoint_id,
        "job_id": args.job_id,
        "title": title,
        "path": str(path),
        "status": "resolved" if audit_data["complete"] else "in-progress",
        "next_role": audit_data.get("next_role", ""),
        "next_command": audit_data.get("next_command", ""),
        "resume_hint": audit_data.get("resume_hint", ""),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"CHECKPOINT {checkpoint_id} {path}")
        if payload["resume_hint"]:
            print("Resume hint: " + payload["resume_hint"])
    return 0


def checkpoint_rows(conn: sqlite3.Connection, workspace: str | None = None, job_id: int | None = None) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if workspace:
        clauses.append("jobs.workspace = ?")
        params.append(str(Path(workspace).expanduser().resolve(strict=False)))
    if job_id is not None:
        clauses.append("checkpoints.job_id = ?")
        params.append(job_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return conn.execute(
        f"""
        SELECT checkpoints.*, jobs.workspace, jobs.goal
        FROM checkpoints
        JOIN jobs ON jobs.id = checkpoints.job_id
        {where}
        ORDER BY checkpoints.created_at DESC, checkpoints.id DESC
        """,
        params,
    ).fetchall()


def list_checkpoints(args: argparse.Namespace) -> int:
    with closing(connect(args.db)) as conn:
        rows = checkpoint_rows(conn, args.workspace, args.job_id)
    payload = {"checkpoints": [row_to_dict(row) for row in rows]}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print("No checkpoints.")
        for row in rows:
            print(f"{row['id']}. job {row['job_id']} {row['created_at']} {row['title']} [{row['status']}]")
    return 0


def load_checkpoint_row(conn: sqlite3.Connection, checkpoint_id: int | None = None) -> sqlite3.Row:
    if checkpoint_id is not None:
        row = conn.execute(
            """
            SELECT checkpoints.*, jobs.workspace, jobs.goal
            FROM checkpoints
            JOIN jobs ON jobs.id = checkpoints.job_id
            WHERE checkpoints.id = ?
            """,
            (checkpoint_id,),
        ).fetchone()
    else:
        rows = checkpoint_rows(conn)
        row = rows[0] if rows else None
    if row is None:
        raise SystemExit("ERROR: checkpoint not found")
    return row


def restore_checkpoint(args: argparse.Namespace) -> int:
    with closing(connect(args.db)) as conn:
        row = load_checkpoint_row(conn, args.checkpoint_id)
    path = Path(row["path"])
    content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    payload = {**row_to_dict(row), "content": content}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(content or f"Checkpoint file missing: {path}")
    return 0


def checkpoint_report(args: argparse.Namespace) -> int:
    with closing(connect(args.db)) as conn:
        rows = list(reversed(checkpoint_rows(conn, job_id=args.job_id)))
    parts = [f"# Checkpoint Report: job {args.job_id}", ""]
    for row in rows:
        path = Path(row["path"])
        content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else f"(missing checkpoint file: {path})"
        parts.extend([f"## {row['created_at']} - {row['title']}", "", content, ""])
    report = "\n".join(parts)
    payload = {"job_id": args.job_id, "checkpoint_count": len(rows), "content": report}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(report)
    return 0


def init_db(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    conn.close()
    print(f"DB initialized: {db_path(args.db)}")


def print_command_contract(contract: dict[str, Any]) -> None:
    print(f"{contract['name']}: {contract['summary']}")
    print(f"Command: {contract['command']}")
    print(f"Writes: {contract['writes']}")
    print(f"Use when: {contract['use_when']}")
    print(f"Failure hint: {contract['failure_hint']}")
    examples = contract.get("examples") or []
    if examples:
        print("Examples:")
        for item in examples:
            print(f"  {item}")


def show_command_contract(args: argparse.Namespace) -> int:
    workspace = args.workspace or default_workspace()
    if args.list or not args.name:
        payload = {"commands": command_catalog.names()}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("\n".join(payload["commands"]))
        return 0

    contract = command_catalog.get_contract(args.name, workspace)
    if contract is None:
        available = ", ".join(command_catalog.names())
        raise SystemExit(f"ERROR: unknown command contract: {args.name}. Available: {available}")
    payload = contract.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_command_contract(payload)
    return 0


def version_text() -> str:
    version_path = REPO_ROOT / "VERSION"
    if not version_path.exists():
        return ""
    return version_path.read_text(encoding="utf-8", errors="replace").strip()


def doctor(args: argparse.Namespace) -> int:
    workspace = args.workspace or default_workspace()
    db = db_path(args.db)
    payload = {
        "ok": True,
        "version": version_text(),
        "workspace": str(Path(workspace).expanduser().resolve(strict=False)),
        "python": sys.executable,
        "taskctl_script": str(SCRIPT_DIR / "taskctl.py"),
        "focus_guard_script": str(SCRIPT_DIR / "focus_guard.py"),
        "db": str(db),
        "db_exists": db.exists(),
        "codex": shutil.which("codex") or shutil.which("codex.cmd") or shutil.which("codex.exe") or "",
        "command_count": len(command_catalog.names()),
        "commands": command_catalog.names(),
        "next_command": command_catalog.next_command("command", workspace),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"cc-router-codex {payload['version'] or 'unknown'}")
        print(f"Workspace: {payload['workspace']}")
        print(f"Task DB: {payload['db']} ({'exists' if payload['db_exists'] else 'missing'})")
        print(f"Python: {payload['python']}")
        print(f"Codex: {payload['codex'] or 'not found'}")
        print("Commands: " + ", ".join(payload["commands"]))
        print(f"Inspect a command: {payload['next_command']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SQLite control plane for Codex worker tasks")
    parser.add_argument("--db", default=None, help="SQLite database path (default: .claude/taskctl.sqlite3)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="initialize the SQLite database")
    p.set_defaults(func=init_db)

    p = sub.add_parser("command", help="print a machine-readable command contract")
    p.add_argument("name", nargs="?", help="command contract name, e.g. capability")
    p.add_argument("-w", "--workspace", default=None)
    p.add_argument("--list", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=show_command_contract)

    p = sub.add_parser("doctor", help="show command and environment diagnostics")
    p.add_argument("-w", "--workspace", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=doctor)

    p = sub.add_parser("checkpoint-save", help="save a resumable checkpoint for a job")
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=save_checkpoint)

    p = sub.add_parser("checkpoint-list", help="list resumable checkpoints")
    p.add_argument("-w", "--workspace", default=None)
    p.add_argument("--job-id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=list_checkpoints)

    p = sub.add_parser("checkpoint-restore", help="restore a checkpoint by id or latest")
    p.add_argument("checkpoint_id", nargs="?", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=restore_checkpoint)

    p = sub.add_parser("checkpoint-report", help="merge checkpoints for a job into one report")
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=checkpoint_report)

    p = sub.add_parser("submit-atomic", help="debug/recovery: create an empty job without executing a capability")
    p.add_argument("goal")
    p.add_argument("-w", "--workspace", default=None)
    p.add_argument("-p", "--priority", default="normal")
    p.add_argument("--constraint", action="append")
    p.add_argument("--acceptance", action="append")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=submit_atomic_job)

    p = sub.add_parser("submit-auto", help="debug/recovery: create an empty job; normal production uses capability")
    p.add_argument("goal")
    p.add_argument("-w", "--workspace", default=None)
    p.add_argument("-p", "--priority", default="normal")
    p.add_argument("--constraint", action="append")
    p.add_argument("--acceptance", action="append")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=submit_auto_job)

    p = sub.add_parser("capability", help="validate, store, execute, and audit exactly one capability step")
    p.add_argument("--job-id", type=int, help="append one capability step to an existing job; otherwise create a job")
    p.add_argument("--goal", help="job goal when creating a new job")
    p.add_argument("-w", "--workspace", default=None)
    p.add_argument("-p", "--priority", default="normal")
    p.add_argument("--constraint", action="append")
    p.add_argument("--acceptance", action="append")
    p.add_argument("--role", required=True, choices=list(ROLES))
    p.add_argument("--title", required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument(
        "--artifact",
        action="append",
        help="required artifact as kind:path, e.g. html:sample-page.html; auto-recorded if the file exists",
    )
    p.add_argument("--required-artifact", action="append", help="required artifact kind or kind:path")
    p.add_argument("-m", "--mode", default="workspace", choices=["sandbox", "workspace", "readonly"])
    p.add_argument("--timeout", type=int, default=DEFAULT_RUN_TIMEOUT_SECONDS)
    p.add_argument("--route-token", help="short-lived token proving this capability matches recent LLM router output")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run_capability)

    p = sub.add_parser("status", help="show jobs or one job")
    p.add_argument("job_id", nargs="?", type=int)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--events", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=status)

    p = sub.add_parser("run-next", help="debug/recovery: run one queued task created by enqueue")
    p.add_argument("job_id", type=int)
    p.add_argument("-m", "--mode", default="workspace", choices=["sandbox", "workspace", "readonly"])
    p.add_argument("--timeout", type=int, default=DEFAULT_RUN_TIMEOUT_SECONDS)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=run_next)

    p = sub.add_parser("complete-task", help="mark a task done")
    p.add_argument("task_id", type=int)
    p.add_argument("--summary", required=True)
    p.set_defaults(func=complete_task)

    p = sub.add_parser("fail-task", help="mark a task failed")
    p.add_argument("task_id", type=int)
    p.add_argument("--summary", required=True)
    p.add_argument("--retryable", action="store_true")
    p.set_defaults(func=fail_task)

    p = sub.add_parser("retry-task", help="requeue a failed, blocked, or stale running task")
    p.add_argument("task_id", type=int)
    p.add_argument("--summary", default="")
    p.add_argument("--stale-after", type=int, default=DEFAULT_STALE_AFTER_SECONDS)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=retry_task)

    p = sub.add_parser("cancel-job", help="cancel all unfinished tasks in a job")
    p.add_argument("job_id", type=int)
    p.add_argument("--summary", default="")
    p.set_defaults(func=cancel_job)

    p = sub.add_parser("enqueue", help="debug/recovery: enqueue one task; normal production uses capability")
    p.add_argument("job_id", type=int)
    p.add_argument("--role", required=True, choices=list(ROLES))
    p.add_argument("--title", required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument("--required-artifact", action="append", help="artifact kind this atomic task must record before it can pass")
    p.set_defaults(func=enqueue)

    p = sub.add_parser("filter-input", help="debug/recovery: validate a worker task input without executing it")
    p.add_argument("--role", required=True, choices=list(ROLES))
    p.add_argument("--title", required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument("--required-artifact", action="append")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=filter_input)

    p = sub.add_parser("review", help="record a review result")
    p.add_argument("job_id", type=int)
    p.add_argument("--verdict", required=True)
    p.add_argument("--issues-json", default="[]")
    p.add_argument("--tests-json", default="[]")
    p.set_defaults(func=add_review)

    p = sub.add_parser("artifact", help="record a task artifact path in SQLite")
    p.add_argument("task_id", type=int)
    p.add_argument("--kind", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--summary", default="")
    p.set_defaults(func=add_artifact)

    p = sub.add_parser("experience-add", help="record a reusable experience candidate from a worker task")
    p.add_argument("--task-id", type=int, required=True)
    p.add_argument("--kind", required=True, choices=EXPERIENCE_KINDS)
    p.add_argument("--title", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--evidence", default="")
    p.add_argument("--reuse", default="")
    p.add_argument("--tag", action="append")
    p.add_argument("--confidence", type=int, default=3)
    p.add_argument("--source-path", default="")
    p.add_argument("--supersedes", type=int)
    p.set_defaults(func=add_experience)

    p = sub.add_parser("experience-list", help="search reusable experiences")
    p.add_argument("--job-id", type=int)
    p.add_argument("--task-id", type=int)
    p.add_argument("--workflow")
    p.add_argument("--role")
    p.add_argument("--kind", choices=EXPERIENCE_KINDS)
    p.add_argument("--status", action="append", choices=sorted(EXPERIENCE_STATUSES), help="default: accepted")
    p.add_argument("--tag", action="append")
    p.add_argument("--query")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=list_experiences)

    p = sub.add_parser("experience-accept", help="promote a reusable experience into the accepted knowledge base")
    p.add_argument("experience_id", type=int)
    p.add_argument("--confidence", type=int)
    p.add_argument("--tag", action="append")
    p.add_argument("--supersedes", type=int, action="append")
    p.set_defaults(func=accept_experience)

    p = sub.add_parser("experience-reject", help="reject a vague, wrong, duplicate, or stale experience")
    p.add_argument("experience_id", type=int)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=reject_experience)

    p = sub.add_parser("experience-prune", help="delete rejected, superseded, or stale experiences")
    p.add_argument("--status", action="append", choices=sorted(EXPERIENCE_STATUSES), help="default: rejected, superseded, stale")
    p.set_defaults(func=prune_experiences)

    p = sub.add_parser("experience-sync-skill", help="regenerate the compact learned-experience skill from accepted experiences")
    p.add_argument("--skill-dir")
    p.add_argument("--workflow")
    p.add_argument("--limit", type=int, default=80)
    p.add_argument("--no-plugin-mirror", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=sync_experience_skill)

    p = sub.add_parser("audit", help="audit whether a job has completed the required worker loop")
    p.add_argument("job_id", type=int)
    p.add_argument("--quality", action="store_true", help="also validate role-specific Markdown artifact structure")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
