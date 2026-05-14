# Project Control Rules

This repository uses `.claude` as a mandatory Python/SQLite control plane.
These rules apply even when no skill is explicitly invoked.

## Default Production Path

- The controller model must not directly create or modify product files.
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
  configurable. Do not replace semantic routing or role-boundary inference with
  regex/keyword rules.
- The router may suggest a task-specific role composition such as `uiux ->
  fullstack -> tester`, but this is advisory, not a fixed workflow. The
  controller runs only one `capability` at a time, inspects the result, then
  decides whether to run, skip, or revise the next capability.
- For production work, use exactly one atomic capability command:

```bash
python .claude/scripts/taskctl.py capability --role <role> --title "<title>" --prompt "<bounded worker prompt>" --artifact <kind:path> --workspace "<workspace>" --goal "<user goal>"
```

- `capability` validates the worker prompt, stores one SQLite job/task, runs one
  Codex worker, auto-records expected artifact paths when files exist, and
  prints the audit result.
- Do not manually expand normal work into `submit-auto`, `filter-input`,
  `enqueue`, and `run-next`. Those commands are debugging/recovery primitives.
- Do not use fixed workflows, `submit-frontend`, `submit-system`,
  `submit-architecture`, `import-plan`, `run-job`, or dependency chains.

## Responsibilities

- Controller model: choose one bounded capability input, run `taskctl.py
  capability`, inspect the result, and decide whether another capability is
  needed.
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
  place, people, or atmosphere visuals. SVG is limited to icons, logos,
  diagrams, and small functional marks unless the selected design reference
  explicitly requires otherwise.
- If suitable local/open-license media is missing, use generated bitmap assets
  as a localization path instead of remote hotlinks or empty placeholders.
  UI/UX should record an `asset_generation_brief` with prompts, style
  constraints, dimensions, and intended file paths; the implementing Codex
  worker should create or place the generated files under a local project asset
  directory and record a `local_asset_manifest`. Product pages should reference
  those local files only.

## Completion Rules

- A task is not successful just because Codex exits successfully. Required
  artifacts must exist and be recorded.
- Before claiming completion, run or rely on the `capability` audit result. If
  needed, run:

```bash
python .claude/scripts/taskctl.py audit <job_id>
```

- Recover stuck workers only through `retry-task` or `cancel-job`; do not edit
  SQLite directly.
