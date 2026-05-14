---
name: decompose
description: Run exactly one validated Codex capability through the Python/SQLite task control plane.
user-invocable: true
---

# SQLite Task Control Submitter

This skill is a thin control-plane entrypoint. These rules are mandatory even
when the user does not explicitly invoke the skill. The main model may compose
one bounded capability-step input at a time, but must not implement directly.
User prompt routing is LLM-backed through `.claude/scripts/llm_router.py`, with
provider/model settings loaded from `.claude/.env`. Codex CLI `gpt-5.4-mini`
with an output schema is preferred for stable routing and task-input guard JSON.
Do not replace routing, role inference, or role-boundary judgment with
regex/keyword rules.
The router may suggest a task-specific role composition, but the main model must
execute only one `capability` at a time and decide the next step after seeing
the result. Do not enqueue the whole composition as a workflow.

## Required Flow

1. Compose exactly one next capability step. Run it through the atomic
   capability command:

```bash
python .claude/scripts/taskctl.py capability --role <role> --title "<title>" --prompt "<bounded worker prompt>" --artifact <kind:path> --workspace "<workspace>" --goal "<user goal>"
```

`capability` validates the main-model-authored prompt, stores one SQLite
job/task, executes one Codex worker, auto-records expected artifact paths when
files exist, and prints the audit result. Do not batch tasks, import a plan, add
dependencies, or create a fixed workflow. After one capability finishes, inspect
SQLite only if another capability is needed.

Frontend visual design is not hard-coded. If the project has `DESIGN.md`,
design tokens, style guide docs, screenshots, theme files, Storybook, or
component docs, Codex workers must use those as authoritative. If not, the UI/UX
worker must run:

```bash
python .claude/scripts/sync_design_refs.py --quiet
```

Then it must select suitable references from `.claude/design-references`,
including the synced DESIGN.md repositories listed in the manifest, based on
target users, domain, product type, and interaction density, and record a
`design_reference_selection` artifact before prototype or implementation. It
must also record a `style_contract` artifact.
The selected reference may be stitched from compatible sources, but every
color, type, surface, spacing, component state, motion, density, icon, and media
decision must be traceable to the selected reference or project source. Do not
add untraceable decorative styling from model taste.

Frontend media should prefer project-provided or open-license/free-use
high-quality images/video for product, place, people, or atmosphere. SVG should
be used sparingly for icons, logos, diagrams, and tiny functional marks; do not
use hand-drawn SVG/CSS shapes as the main hero or product visual when real media
is more appropriate.

2. Show compact state only when needed:

```bash
python .claude/scripts/taskctl.py status <job_id>
```

3. If a worker fails, becomes stale, lacks required artifacts, or hits a Windows
   sandbox error, recover through:

```bash
python .claude/scripts/taskctl.py retry-task <task_id> --summary "<why>"
python .claude/scripts/taskctl.py cancel-job <job_id> --summary "<why>"
```

4. During closure, review experience candidates:

```bash
python .claude/scripts/taskctl.py experience-list --job-id <job_id> --status candidate --json
python .claude/scripts/taskctl.py experience-accept <experience_id> --tag <capability>
python .claude/scripts/taskctl.py experience-reject <experience_id> --reason "not reusable"
python .claude/scripts/taskctl.py experience-sync-skill
```

5. Before reporting completion, run:

```bash
python .claude/scripts/taskctl.py audit <job_id>
```

## Responsibilities

- ds v4: submit jobs, compose one capability input at a time, inspect SQLite state, decide continue/stop/user escalation.
- Python taskctl: persist jobs/tasks/runs/artifacts/events/reviews in SQLite.
- Codex workers: planning, divergence, requirements, prototype, UI/UX, full-stack
  implementation, tests, review, and closure.
- Experience loop: every worker can record concise reusable lessons with
  `experience-add`; reviewer/closer curates them; `experience-sync-skill`
  regenerates the compact `learned-experience` skill from accepted lessons.

## Role Boundaries

The Python input filter enforces this table before Codex can run. Deterministic
checks cover artifact syntax/path safety; semantic boundary decisions go
through the LLM task-input guard:

- `planner`: plans, inventories, sequencing notes, architecture notes.
  No product source files or implementation artifacts.
- `divergent`: options, tradeoff analysis, alternatives. No product source
  files or implementation artifacts.
- `requirements`: requirements, acceptance checks, constraints. No product
  source files or implementation artifacts.
- `uiux`: design artifacts only, such as `style_inventory`,
  `design_reference_selection`, `component_map`, `style_contract`, and visual
  review notes. No `.html`, `.css`, `.js`, `.tsx`, backend source, schema, or
  migration edits.
- `prototype`: prototype specifications, DOM/interaction contracts, behavior
  notes. No production UI code.
- `fullstack`: the only role that may create or modify product implementation
  code, including frontend, backend, database, scripts, and production HTML.
- `tester`: verification reports, screenshots, and test files under test paths.
  No production source edits.
- `reviewer`: review findings and risk reports only. No patches.
- `closer`: closure/audit summaries only. No patches.

## Rules

- Do not create product files in ds context. Execute bounded worker prompts only
  through `taskctl.py capability`.
- Do not bypass SQLite for task state.
- Do not read full logs unless `status`, `audit`, or a failed task indicates it is needed.
- Worker tasks must be executed one at a time through `taskctl.py capability`.
- Every worker prompt must pass the task input filter before Codex execution.
- Do not manually choose worker models in ds context. Let `capability` apply
  `.claude/model_policy.json`, except for explicit emergency overrides through
  `CODEX_MODEL` or `CODEX_REASONING_EFFORT`.
- Do not let experience accumulate uncurated. Accept only specific,
  evidence-backed, reusable lessons. Reject vague, wrong, duplicate, or stale
  lessons and prune rejected/superseded/stale rows when appropriate.
- For existing backend/admin systems, do not use imagegen as the style source of
  truth. Reuse existing components and style tokens first.
- For frontend work without project design specs, do not invent visual style
  from memory. Sync design references and choose a fitting DESIGN.md precedent.
- For frontend implementation, reject pages whose visual style is not traceable
  to the selected design reference or project style source. Open-license media
  source/license notes and any SVG usage justification must be recorded.
- Do not use fixed workflows, `import-plan`, `run-job`, or dependency chains.
  Composition is solely a main-model decision between single-step executions.
  Do not manually expand normal production work into submit/filter/enqueue/run
  command sequences.
