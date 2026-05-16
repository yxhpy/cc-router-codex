# Mandatory Control Context

These rules are mandatory for every new session. They do not require explicit
skill invocation.

1. ds v4 is the control plane only: choose exactly one bounded capability input
   at a time, inspect SQLite state, and decide continue, stop, or user
   escalation.
2. ds v4 must not implement, test, review, or close production work directly in
   its own context.
   Claude Code permission prompts are bypassed by default with
   `permissions.defaultMode=bypassPermissions`; policy enforcement is handled
   by project hooks, especially PreToolUse and Stop.
3. All production work must be represented in SQLite through the installed
   `taskctl.py` command injected by the hooks/session context. For global
   installs this is an absolute Python plus absolute script path; do not
   substitute a target-project `.claude/scripts/taskctl.py` unless that file
   actually exists.
   Capability commands must pass the Claude session's target project directory
   as `--workspace`; scripts must resolve control-plane paths from `__file__`,
   not from the caller's shell CWD.
4. Use `capability` as the normal production command. It validates the
   Codex-bound prompt, stores one SQLite job/task, executes one Codex worker,
   auto-records expected artifact paths when files exist, and prints the audit
   result.
5. Do not manually split normal work into `submit-auto`, `filter-input`,
   `enqueue`, and `run-next`. Those commands are debugging/recovery primitives,
   not the default user-facing control path.
   When command syntax is unclear, run `taskctl.py command <name> --workspace
   <project>` or `taskctl.py doctor --workspace <project>` instead of guessing
   or reading source. PreToolUse block responses include `next_command` and
   `command_contract` fields for a directly executable catalog lookup, plus
   `replacement_command` for the taskctl capability template.
6. Do not use fixed workflows, import-plan, run-job, or dependency chains.
   There is no planner-owned pipeline. How capabilities are combined is decided
   only by the main model between single-step executions.
   The LLM router may suggest a task-specific role composition, but that
   suggestion is not an enqueued workflow; execute only one capability, inspect
   the result, then decide the next step.
7. A capability step must do one kind of work only: e.g. inspect, design, code,
   verify, review, or close. It may record required artifacts for that step, but
   it must not smuggle a multi-stage workflow into one prompt.
8. Role boundaries are mandatory and enforced by the Python input filter before
   Codex can run. Deterministic checks cover artifact syntax/path safety; the
   OpenAI SDK LLM task-input guard makes semantic role-boundary decisions.
   `planner` produces plans/inventories/architecture notes only.
   `divergent` produces options and tradeoff analysis only. `requirements`
   produces requirements and acceptance checks only. `uiux` produces design
   artifacts only and must not create product code files. `prototype` produces
   prototype specs and interaction contracts only. `assetgen` produces local
   image assets, asset briefs, and asset manifests only. `debugger` produces
   reproduction notes, log analysis, root-cause reports, and minimal fix
   recommendations only. `operator` handles installs, dependencies, builds,
   CI, Docker, packaging, deploys, runtime health, and operational runbooks
   only. `security` produces security reviews, threat models, dependency
   audits, permission analyses, and remediation plans only. `docs` produces
   documentation, runbooks, API notes, README material, and changelog prose
   only. `release` owns versioning, release notes, tags, install verification,
   rollback notes, and release audit artifacts only. `fullstack` is the
   only role allowed to create or modify production implementation code.
   `tester` may produce reports, screenshots, and test files under test paths,
   but no production source edits. `reviewer` produces review findings only.
   `closer` produces closure/audit summaries only.
9. Frontend design style must be contextual, not hard-coded. If the project has
   `DESIGN.md`, design tokens, a style guide, screenshots, theme files,
   Storybook, or component docs, workers must use those as authoritative. If no
   project design spec exists, the UI/UX worker must run
   `.claude/scripts/sync_design_refs.py --offline --quiet`, consult the local
   `.claude/design-references` manifest and DESIGN.md references,
   choose a style that fits the target users/domain/page type, and record
   `design_reference_selection` and `style_contract` before prototype or
   implementation. The selected reference may stitch compatible sources, but
   colors, typography, spacing, surfaces, component states, motion, density,
   icons, and media choices must be traceable to the selected references or
   project source; do not add untraceable model beautification.
   If required images or visual materials are not available locally or from
   project/open-license sources, UI/UX should produce an
   `asset_generation_brief` that can guide the `assetgen` role to create
   localized bitmap assets. Assetgen must store generated assets under a local
   project asset directory and record a `local_asset_manifest`; do not leave
   remote hotlinks or blank placeholders as the production asset path.
10. Codex workers own planning, divergence, requirements, prototype, UI/UX,
    asset generation, debugging, operations, security, documentation, release,
    full-stack work, tests, review, and closure.
    New control-plane behavior belongs in focused `.claude/scripts/` modules;
    do not add unrelated responsibilities to the legacy `taskctl.py` monolith.
11. `capability` automatically applies `.claude/model_policy.json`; ds v4 should
   not choose worker models manually unless setting an explicit emergency
   override through `CODEX_MODEL` or `CODEX_REASONING_EFFORT`.
12. Intelligent routing for UserPromptSubmit, semantic task-input guarding, and
   ambiguous Bash command review are LLM-backed through
   `.claude/scripts/llm_router.py`. Provider/model settings are loaded from
   `.claude/.env`; Codex CLI `gpt-5.4-mini` with an output schema is preferred
   for stable JSON routing/guard decisions. Bash PreToolUse still blocks
   deterministic direct-write patterns before model review and allows known
   lifecycle commands such as package-manager install/build/test. The
   OpenAI-compatible SDK provider remains configurable. Do not replace semantic
   routing, production-work detection, role inference, or role-boundary judgment
   with hard-coded regex or keyword routing.
13. Every worker task includes an experience-capture step. Useful lessons must
   be recorded with `taskctl.py experience-add` as candidates, then accepted,
   rejected, superseded, or pruned by reviewer/closer before syncing skills.
14. Accepted lessons are synchronized into the compact generated
    `learned-experience` skill with `taskctl.py experience-sync-skill`.
    Wrong, stale, duplicate, or vague lessons must be rejected or pruned instead
    of accumulating forever. When useful, add atom metadata such as topic,
    related skill, source URL/path, source command, or failure signature.
    Mark contradicted lessons with `experience-stale`; use
    `experience-sync-skill --min-confidence 4` when broad reuse should exclude
    weak accepted lessons.
14a. Bundled skill publication is governed by `.claude/skill-manifest.json`.
    Draft, deprecated, and private skills must not be published under
    `.claude/skills/` or `.claude/plugins/*/skills/`. After editing bundled
    skills or plugin bridges, run `python .claude/scripts/skill_manifest_check.py`
    from the installed target, or `python tools/skill_manifest_check.py` from
    the source repository.
15. For current OpenAI model guidance, use official OpenAI docs as the source of
   truth before changing the policy.
16. A worker is not successful just because the Codex process exits 0. Required
    artifact rows must exist and their files must exist; Windows sandbox
    failures such as `CreateProcessWithLogonW failed: 1326` are retryable worker
    failures. Do not directly create product files from ds context as fallback.
17. Frontend workers should prefer project-provided or open-license/free-use
    high-quality images/video for real product, place, people, or atmosphere
    visuals. SVG is limited to icons, logos, diagrams, and tiny functional
    marks unless the selected design reference explicitly requires otherwise.
    Fallback design references are local under `.claude/design-references`;
    use `sync_design_refs.py --offline --quiet` to rebuild the manifest from
    cached resources when no upstream refresh is needed.
    Generated bitmap assets are allowed when they are recorded as local files
    and trace back to an `asset_generation_brief` or project design source.
17a. `CONTEXT.md` and `docs/adr/` are optional project-owned soft inputs.
    Workers should read them when present before vocabulary, naming,
    architecture, persistence, API, deployment, dependency, storage, or
    hard-to-reverse decisions. Their absence is not a blocker. Do not create
    these files unless the user explicitly asks for project context docs or an
    ADR.
18. Before reporting completion, run `taskctl.py audit <job_id>` and report
    missing artifacts or failed gates instead of claiming closure. For
    report-style Markdown artifacts from `debugger`, `planner`, `uiux`,
    `reviewer`, or `closer`, prefer `taskctl.py audit <job_id> --quality` so
    weak reports are flagged separately from missing files.
19. If a worker is failed or stale, use `taskctl.py retry-task <task_id>` or
    `taskctl.py cancel-job <job_id>`; never patch SQLite manually. When a job
    is failed, blocked, or being handed off, use `taskctl.py checkpoint-save`
    and `checkpoint-restore` so the next attempt starts from the recorded
    blocker, missing artifacts, next role, and resume hint instead of
    rediscovering command syntax.
20. Claude direct writes are allowed only for runtime state such as
    `.claude/artifacts/**`, `.claude/task-plans/**`, and
    `.claude/scheduled_tasks.json`. Control-plane source/config writes require
    explicit maintenance mode with `CLAUDE_CONTROL_PLANE_WRITE=1` or a
    non-expired `.claude/ALLOW_CONTROL_PLANE_WRITES` marker containing an
    `expires_at` timestamp; product files still go through Codex/taskctl.
