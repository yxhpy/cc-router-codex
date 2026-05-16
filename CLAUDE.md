# Project Control Rules

This repository uses `.claude` as a mandatory Python/SQLite control plane.
These rules apply even when no skill is explicitly invoked.

## Default Production Path

- The controller model must not directly create or modify product files.
- Claude Code permission prompts are bypassed by default through
  `permissions.defaultMode=bypassPermissions`; policy enforcement is handled by
  the project hooks, especially PreToolUse and Stop.
- `.claude` is not a blanket write escape hatch. Direct Claude writes are
  limited to runtime state documented in `.claude/CONTROL_PLANE_POLICY.md`;
  control-plane source/config writes require explicit maintenance mode.
- Control-plane scripts resolve the repository root from their own file path.
  Generated capability commands must pass the Claude session's target project
  directory as `--workspace`; the hook script path may be absolute and live in
  this control-plane repository.
- User prompt routing and semantic task-input guarding are LLM-backed through
  `.claude/scripts/llm_router.py`. Provider/model settings live in
  `.claude/.env`; Codex CLI `gpt-5.4-mini` with an output schema is preferred
  for stable routing/guard JSON. The OpenAI-compatible SDK path remains
  configurable. Generated router commands may include a short-lived
  `--route-token` so `taskctl.py capability` can reuse the exact recent LLM
  routing decision instead of running a second classifier; deterministic
  role/artifact checks and `safety_filter.py` still run. Do not replace
  semantic routing or role-boundary inference with regex/keyword rules.
- The router may suggest a task-specific role composition such as `uiux ->
  assetgen -> fullstack -> tester`, but this is advisory, not a fixed workflow. The
  controller runs only one `capability` at a time, inspects the result, then
  decides whether to run, skip, or revise the next capability.
- For production work, use exactly one atomic capability command. Use the
  installed `taskctl.py` command shown by the hook/session context; when this
  control plane is installed globally, that command is an absolute Python plus
  absolute script path. Do not substitute a target-project
  `.claude/scripts/taskctl.py` path unless that file actually exists.

```bash
<installed-taskctl-command> capability --role <role> --title "<title>" --prompt "<bounded worker prompt>" --artifact <kind:path> --workspace "<workspace>" --goal "<user goal>"
```

- `capability` validates the worker prompt, stores one SQLite job/task, runs one
  Codex worker, auto-records expected artifact paths when files exist, and
  prints the audit result.
- Hook-generated `capability` commands may also pass `--route-token <token>` to
  avoid repeating the same LLM route/guard decision for an unchanged step.
- Do not manually expand normal work into `submit-auto`, `filter-input`,
  `enqueue`, and `run-next`. Those commands are debugging/recovery primitives.
- Do not guess control-plane command syntax or read source just to discover a
  command. Use `taskctl.py command <name> --workspace "<workspace>"` or
  `taskctl.py doctor --workspace "<workspace>"`; PreToolUse blocks include
  directly executable `next_command` / `command_contract` fields and a
  `replacement_command` template.
- Do not use fixed workflows, `submit-frontend`, `submit-system`,
  `submit-architecture`, `import-plan`, `run-job`, or dependency chains.

## Responsibilities

- Controller model: choose one bounded capability input, run `taskctl.py
  capability`, inspect the result, and decide whether another capability is
  needed.
- Focus guard: production goals remain active in
  `.claude/task-plans/focus_state.json` until the controller records
  `focus_guard.py complete` with result evidence or `focus_guard.py exhausted`
  with attempted routes and blockers. The Stop hook blocks final answers while
  this state is active.
- Codex worker: perform the actual planning, design, implementation, testing,
  review, or closure work inside the capability step.
- SQLite/taskctl: persist jobs, tasks, runs, artifacts, reviews, and experience.
- New control-plane logic should be added as focused modules under
  `.claude/scripts/`; do not keep adding unrelated responsibilities to
  `taskctl.py`.

## Role Boundaries

The Python input filter enforces these boundaries before Codex can run. It uses
deterministic checks for artifact syntax/path safety and the LLM guard for
semantic role-boundary judgment:

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
- `assetgen`: local raster image assets only, generated through the built-in
  `.claude/scripts/assetgen_exec.py` Codex backend. Before generation it must
  run `.claude/scripts/prompt_template_mcp.py` to fast-check or install the
  local `image-2-prompt` full-profile MCP under `.prompt-searcher`, retrieve
  suitable prompt templates, and feed that template context into the image
  model. The MCP version standard is documented in `VERSIONING.md`: compare the
  installed git commit with the latest known commit and warn if the user may
  need to upgrade; do not auto-upgrade during generation. Assetgen covers game
  assets, web visuals, video thumbnails/key art/overlays, icons, textures,
  sprites, product renders,
  `asset_generation_brief`, and `local_asset_manifest`. No SVG outputs or
  product code files.
- `fullstack`: the only role that may create or modify product implementation
  code, including frontend, backend, database, scripts, and production HTML.
- `tester`: verification reports, screenshots, and test files under test paths.
  No production source edits.
- `reviewer`: review findings and risk reports only. No patches.
- `closer`: closure/audit summaries only. No patches.

## Frontend Rules

- If the project has `DESIGN.md`, design tokens, a style guide, screenshots,
  theme files, Storybook, or component docs, those project sources are
  authoritative.
- Fallback design resources are local under `.claude/design-references`.
  `manifest.json` must stay portable with relative paths. Rebuild it from the
  local cache with `python .claude/scripts/sync_design_refs.py --offline --quiet`
  when network refresh is not required.
- If no project design spec exists, the UI/UX capability must run
  `.claude/scripts/sync_design_refs.py --offline --quiet`, choose suitable references from
  `.claude/design-references`, and record `design_reference_selection` and
  `style_contract` artifacts.
- UI/UX workers must not invent visual style. Colors, typography, spacing,
  surfaces, component states, motion, density, icons, and media choices must be
  traceable to the project source or selected `.claude/design-references`
  DESIGN.md files.
- Use high-quality project/open-license images or video for real product,
  place, people, or atmosphere visuals. Generated assetgen media must be local
  raster output from `.claude/scripts/assetgen_exec.py`; SVG is limited to
  manually coded icons, logos, diagrams, and small functional marks unless the
  selected design reference explicitly requires otherwise.
- If suitable local/open-license media is missing, use generated bitmap assets
  as a localization path instead of remote hotlinks or empty placeholders.
  UI/UX should record an `asset_generation_brief` with prompts, style
  constraints, dimensions, and intended raster file paths; `assetgen` should
  create the generated files under a local project asset directory with
  `.claude/scripts/assetgen_exec.py` and record a `local_asset_manifest`.
  Assetgen must use `gpt-5.4-mini` for the bounded prompt-template adapter and
  raster-generation prompt. Product pages should reference those local files
  only.

## Completion Rules

- A task is not successful just because Codex exits successfully. Required
  artifacts must exist and be recorded.
- Do not stop after one failed or partial attempt. Record the attempt, inspect
  logs/artifacts, search or try another viable route, and continue.
- Before a final answer for production work, run exactly one focus transition:

```bash
<installed-focus-guard-command> complete --workspace "<workspace>" --evidence "<artifacts/tests/result>"
```

or, only after all viable routes have been tried:

```bash
<installed-focus-guard-command> exhausted --workspace "<workspace>" --evidence "<attempts, searches, blockers>"
```

The Stop hook blocks final answers until one of these states is recorded.
- Before claiming completion, run or rely on the `capability` audit result. If
  needed, run:

```bash
<installed-taskctl-command> audit <job_id>
```

- For report-style Markdown artifacts produced by `debugger`, `planner`,
  `uiux`, `reviewer`, or `closer`, prefer:

```bash
<installed-taskctl-command> audit <job_id> --quality
```

- Recover stuck workers only through `retry-task` or `cancel-job`; do not edit
  SQLite directly. For failed, blocked, or handed-off jobs, save and restore
  state with `checkpoint-save`, `checkpoint-list`, `checkpoint-restore`, and
  `checkpoint-report` so the next attempt starts from the recorded blocker and
  resume hint.
