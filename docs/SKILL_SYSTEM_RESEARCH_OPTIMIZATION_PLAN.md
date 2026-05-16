# Skill System Research and Optimization Plan

This note compares three external skill-system repositories against the current
`cc-router-codex` control plane, then turns the useful patterns into a concrete
implementation plan.

## Sources Reviewed

| Repository | Local snapshot | Main pattern |
| --- | --- | --- |
| `mattpocock/skills` | `/tmp/mattpocock-skills` at `e74f006` | Small composable engineering skills, project setup docs, domain glossary, ADRs, TDD and diagnosis loops. |
| `alchaincyf/nuwa-skill` | `/tmp/nuwa-skill` at `ea4b9ab` | Skill factory pipeline: self-contained skill directories, multi-source research, synthesis checkpoints, quality verification, honest boundaries. |
| `dontbesilent2025/dbskill` | `/tmp/dbskill` at `9d54439` | Toolkit router, many small domain tools, local save/restore/report state, open knowledge atom library, agent workspace migration. |
| `cc-router-codex` | current repo | Claude/Codex control plane with hooks, role routing, task input guard, command catalog, artifact audit, focus guard, and learned experiences. |

## Comparative Findings

### 1. Routing Model

`mattpocock/skills` relies on precise `description` frontmatter and small skill
names. Each description explains when to trigger the skill.

`dbskill` adds an explicit router skill (`dbs`) that does no domain work; it
only maps user intent to the right sub-skill. This keeps routing separate from
execution.

`cc-router-codex` already has a stronger runtime router in
`.claude/scripts/llm_router.py`, plus role validation in
`.claude/scripts/task_input_filter.py`. The missing part is durable route
continuation: after a failed or blocked capability, the system has
`next_command`, but not a structured `next_role` / `next_artifact` recovery
record.

Optimization: keep the LLM router, but add task-state fields for the next
recommended role and command after any failed, blocked, or incomplete run.

### 2. Command Discoverability

`mattpocock/skills` uses simple helper scripts such as `list-skills.sh` and
`link-skills.sh`. The scripts are small, obvious, and mostly deterministic.

`dbskill` has a build script that packages each skill into standalone zip files
and includes only referenced knowledge files. This avoids carrying the whole
knowledge base into every installed skill.

`cc-router-codex` already solved the immediate command-guessing problem with
`taskctl command` and `taskctl doctor`. The remaining gap is that command
contracts describe commands, not state transitions. A blocked hook still tells
Claude what command shape to use, but not which previous failed attempt should
be resumed, superseded, or summarized.

Optimization: extend command contracts with `state_input`, `state_output`, and
`next_state` fields. Add contracts for save, restore, and report commands.

### 3. Evidence and Quality Gates

`nuwa-skill` has the strongest evidence discipline. It creates a skill directory
before research, stores six research files inside that directory, pauses at a
research review checkpoint, pauses again after synthesis, then runs quality
checks.

`mattpocock/skills` also pushes evidence discipline through diagnosis and TDD:
build a feedback loop first, reproduce before hypothesizing, write one behavior
test at a time, and avoid implementation-detail tests.

`cc-router-codex` currently audits artifact existence and required artifact
kinds, but it does not score whether an artifact is good enough. The control
plane can know that `root_cause_report.md` exists, but not whether it contains a
reproduction loop, ranked hypotheses, fix evidence, and cleanup notes.

Optimization: add role-specific quality checkers for artifact structure. Start
with non-invasive Markdown validators for:

- `debugger`: feedback loop, reproduction, hypotheses, probe result, fix
  recommendation, regression check.
- `planner`: scope, dependencies, vertical slices, gates, risks.
- `uiux`: design source, style contract, assets needed, states, responsive
  constraints.
- `reviewer`: findings-first structure with file references, severity, tests.
- `closer`: prompt-to-artifact checklist and evidence coverage.

### 4. Domain Language and ADRs

`mattpocock/skills` treats `CONTEXT.md` as a project glossary and `docs/adr/` as
the durable place for hard-to-reverse decisions. This reduces token waste
because future agents can use compact project terms instead of rediscovering
meaning.

`nuwa-skill` uses a similar idea for people and topics: a generated skill is
self-contained and carries its sources, timeline, mental models, and honest
boundaries.

`cc-router-codex` has architecture docs and mandatory context, but no formal
project glossary, no ADR directory, and no worker prompt rule that says “read
the glossary / ADRs first if present.”

Optimization: add a soft project context contract:

- If `CONTEXT.md` exists, workers should use its vocabulary.
- If `docs/adr/` exists, workers should check relevant ADRs before proposing
  architectural changes.
- Do not create these files automatically during install; only create them
  through an explicit `docs` or `planner` capability.

### 5. State Across Conversations

`dbskill` has a practical save/restore/report system. Each save writes a local
Markdown snapshot with YAML frontmatter, project slug, source skill, status,
next skill, conclusions, rejected directions, hypotheses, and recommended next
step.

`cc-router-codex` stores job/task state in SQLite and focus state in JSON, but
there is no user-facing resume artifact. When Claude re-enters after failure,
it often re-reads code or repeats command attempts instead of restoring the last
diagnosis.

Optimization: add a small state management surface:

- `taskctl checkpoint-save --job-id <id> --title <title>`
- `taskctl checkpoint-list [--workspace <path>]`
- `taskctl checkpoint-restore [checkpoint_id]`
- `taskctl checkpoint-report [--job-id <id>]`

The checkpoint should summarize user goal, job id, source role, status,
completed artifacts, missing artifacts, failed command, known blocker,
rejected routes, and recommended next role/command.

### 6. Knowledge Atoms and Learned Experience

`dbskill` exposes an atom library: JSONL records with `knowledge`, source,
topics, skills, type, and confidence. That makes the knowledge base searchable
and composable.

`cc-router-codex` already has `experience-add`, `experience-list`, and
`experience-sync-skill`. This is close to a knowledge atom system, but the
schema is still workflow-oriented and not tied to confidence, affected role, or
source command quality.

Optimization: evolve accepted experiences into structured atoms without
breaking the current skill sync:

- Add optional fields: `role`, `command`, `failure_signature`,
  `confidence`, `source_url_or_path`.
- Keep freeform summary and reuse text for human readability.
- Let `experience-sync-skill` render only accepted high-confidence items by
  default.

### 7. Source of Truth and Bridges

`dbs-agent-migration` is directly relevant. It distinguishes true skill source
from Claude/Codex bridge directories and insists bridge logic should not become
the long-term maintenance point.

`mattpocock/skills` also avoids publishing draft, personal, and deprecated
skills by keeping an explicit plugin manifest.

`cc-router-codex` currently bundles `.claude/skills` and plugin skills, but it
does not have a first-class source-of-truth document for skill publishing
rules, bridge generation, or draft exclusion.

Optimization: add a bridge governance layer:

- `docs/SKILL_SOURCE_OF_TRUTH.md`
- `tools/sync-claude-skills.py`
- `tools/sync-codex-skills.py`
- `tools/skill_manifest_check.py`

This should verify that distributable skills are listed, drafts are excluded,
and Claude/Codex bridge names remain consistent.

## What Not To Absorb

- Do not copy Nuwa's persona roleplay behavior into the control plane. The
  useful part is the research and quality pipeline, not roleplay.
- Do not copy dbskill's business methodology. The useful part is toolkit
  routing, state snapshots, reports, and knowledge atom structure.
- Do not copy mattpocock's user-confirmation-heavy flow wholesale. This user
  prefers direct execution; checkpoints should be used when evidence quality or
  irreversible design choices matter, not before every small action.
- Do not make `CONTEXT.md` or ADRs mandatory for every project. They should be
  soft inputs unless a project opts in.

## Recommended Roadmap

### v0.1.14: Failure Resume and Checkpointing

Goal: reduce repeated command failures and source-reading loops.

Changes:

- Add `taskctl checkpoint-save`, `checkpoint-list`, `checkpoint-restore`, and
  `checkpoint-report`.
- Add a `checkpoints` SQLite table or deterministic Markdown snapshots under
  `.claude/task-plans/checkpoints/`.
- Extend blocked/audit output with `next_role`, `next_artifact`, and
  `resume_hint`.
- Add command catalog entries for the new checkpoint commands.

Tests:

- Checkpoint files are created with stable frontmatter.
- Restore selects by explicit id or latest timestamp.
- Report merges multiple checkpoints by job id.
- Blocked capability emits actionable `next_role` and `resume_hint`.

### v0.1.15: Artifact Quality Validators

Goal: make “artifact exists” less weak as a completion signal.

Status: implemented in `v0.1.15`.

Changes:

- Add `.claude/scripts/artifact_quality.py`.
- Implement role-specific Markdown structure checks for debugger, planner,
  uiux, reviewer, closer.
- Let `taskctl audit --quality` report weak artifacts separately from missing
  artifacts.
- Keep default audit backward-compatible initially.

Tests:

- Valid debugger report passes required section checks.
- Empty or generic report fails with concrete missing sections.
- Quality failures do not masquerade as missing files.

### v0.1.16: Project Context and ADR Soft Contract

Goal: improve worker alignment without forcing every repo to adopt a heavy
process.

Status: implemented in `v0.1.16`.

Changes:

- Add docs explaining optional `CONTEXT.md` and `docs/adr/` usage.
- Add worker prompt footer: if present, read relevant glossary and ADRs before
  architecture or naming decisions.
- Add a `docs` capability template to create/update these files only when
  explicitly requested.

Tests:

- Worker footer mentions glossary/ADR only as soft inputs.
- Installer does not create `CONTEXT.md` or ADR files.

### v0.1.17: Skill Source Governance

Goal: prevent skill drift across Claude/Codex surfaces.

Status: implemented in `v0.1.17`.

Changes:

- Add a skill manifest checker.
- Define distributable, draft, deprecated, and private skill buckets.
- Generate or verify Claude/Codex bridge consistency from one source of truth.

Tests:

- Missing published skill manifest entry fails.
- Draft/deprecated skills are not published.
- Bridge names are deterministic and point to source directories.

### v0.1.18: Installed Skill Checker Hotfix

Goal: keep the `v0.1.17` skill manifest checker usable from installed targets
that do not carry the source repository's top-level `tools/` directory.

Status: implemented in `v0.1.18`.

Changes:

- Let installed `test_all.py` run `.claude/scripts/skill_manifest_check.py`
  when `tools/skill_manifest_check.py` is absent.

### v0.1.19: Installed Skill Manifest Test Hotfix

Goal: keep the skill manifest regression test usable from installed targets
that do not carry the source repository's top-level `tools/` directory.

Status: implemented in `v0.1.19`.

Changes:

- Let installed `test_skill_manifest_check.py` import
  `.claude/scripts/skill_manifest_check.py` when `tools/skill_manifest_check.py`
  is absent.

### v0.1.20: Experience Atoms

Goal: make learned experiences searchable and safer to reuse.

Status: implemented in `v0.1.20`.

Changes:

- Add optional atom-like fields to experiences.
- Render accepted high-confidence experiences first.
- Add stale/conflicting evidence handling.

Tests:

- Existing experience DB migration is backward-compatible.
- Accepted low-confidence items can be hidden from generated skill output.

### v0.1.21: Command State Contracts

Goal: reduce command retries and source-reading loops after hook blocks or
failed capability commands.

Status: implemented in `v0.1.21`.

Changes:

- Add structured `state_input`, `state_output`, and `next_state` fields to
  every command catalog contract.
- Render command state fields in `taskctl command <name>` text and JSON output.
- Keep hooks using the same command catalog lookup path, so blocked commands
  point to contracts with recovery state instead of raw syntax only.

Tests:

- `taskctl command capability --json` exposes state transition fields.
- Every command contract defines nonempty state transition fields.

## Highest Leverage Next Step

The next highest-leverage step is to make hook block messages consume the new
command state contract more directly:

- Include the most relevant `state_input`, `state_output`, and `next_state`
  entries directly in PreToolUse block payloads.
- Add a compact `taskctl command --explain-failure <log-or-code>` helper only
  if source-free recovery still needs another step.
- Keep Stop strict, but make recovery prompts smaller and more mechanical.

This builds on the command catalog and checkpoint work without weakening the
production-write policy.
