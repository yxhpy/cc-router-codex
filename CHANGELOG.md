# Changelog

All notable changes to `cc-router-codex` are tracked here.

## Unreleased

- No changes yet.

## 0.1.35 - 2026-05-18

- Added installed `claude_fast.cmd` / `claude_fast.ps1` launchers for
  lower-latency Claude Code sessions. They keep the configured default model
  while limiting tools, disabling slash-command loading, ignoring unrelated MCP
  configs, and using low effort for fast submission turns.

## 0.1.34 - 2026-05-18

- Compacted the auto-loaded `CLAUDE.md` control rules and moved expanded detail
  behind hook/taskctl enforcement to reduce every Claude Code startup prompt.
- Updated installs to refresh `.claude/CLAUDE.md` as well as root `CLAUDE.md`,
  replacing stale expanded global memory files from older installs.

## 0.1.33 - 2026-05-18

- Changed SessionStart to inject a compact control context by default, removing
  the duplicated expanded rule block that made simple Claude CLI turns start
  with large prompt payloads.
- Added `TASKCTL_SESSION_CONTEXT_PROFILE=compact` with `full` available only
  when expanded startup rules are needed for debugging.

## 0.1.32 - 2026-05-18

- Added a `--speed-profile fast` capability/run-next option that downshifts
  worker model policy for lower-latency interactive runs and automatically uses
  compact worker policy footers.
- Added `--async` capability submission so taskctl can return job/task/pid
  immediately while the worker continues through a background `run-next`.
- Added a deterministic `--validator html-smoke` tester path for local HTML
  checks such as required text, button count, and no `http://` or `https://`
  URLs, avoiding a model worker for simple smoke verification.
- Added environment knobs for hook-suggested fast async commands:
  `TASKCTL_INTERACTIVE_SPEED_PROFILE=fast` and `TASKCTL_INTERACTIVE_ASYNC=1`.

## 0.1.31 - 2026-05-18

- Fixed non-`~/.claude` installs, including `~/.grok`, when they reuse the
  portable Python runtime: Windows `run_python.cmd` now bootstraps the target
  script directory before executing the script, and direct entrypoint scripts
  insert their own directory before importing control-plane modules.
- Clarified frontend UI/UX design-reference instructions so workers run the
  actual control-plane `sync_design_refs.py` command and read the control-plane
  `.claude/design-references/manifest.json`, instead of looking for missing
  scripts or manifests in lightweight project runtimes.
- Added regression coverage for the Windows runner bootstrap and the generated
  worker design-reference prompt.

## 0.1.30 - 2026-05-18

- Changed user-level installs to merge the control plane into `~/.claude`
  instead of replacing the whole directory, preserving existing Claude Code
  settings, sessions, projects, cache, and history while still updating hooks,
  scripts, policies, skills, and release files.
- Added stale project-hook cleanup for global installs: lightweight project
  auto-init now backs up project `.claude/settings.json` files that still point
  at missing project-local cc-router hook scripts and removes those stale hook
  entries so global hooks can route the session.
- Fixed task artifact auditing for recorded wildcard paths such as
  `src/demo04/*`, allowing audits to pass when the wildcard matches existing
  files.
- Relaxed UI/UX Markdown quality checks to accept `Source Basis` as design
  source evidence.
- Added regression coverage for user-level settings merge, stale hook cleanup,
  and wildcard artifact audits.

## 0.1.29 - 2026-05-17

- Changed the Codex foreground server guard to advisory mode by default: it now
  logs `FOREGROUND_SERVER_WARNING` instead of killing the whole capability when
  worker logs contain dev-server readiness text. Hard termination is available
  only with `CODEX_FOREGROUND_SERVER_GUARD_MODE=kill`.
- Detected Grok PreToolUse payloads that arrive through Claude-style
  `tool_name` keys but use Grok internal tool aliases such as
  `run_terminal_command` or `search_replace`, so denied calls remain nonfatal
  Grok `decision=deny` responses.
- Scanned composite shell command segments for inline Python file IO, blocking
  commands such as `cd ...; python -c "Path('data/search-index.json').read_text()"`
  before the Bash guard can allow them.
- Added regressions for nonfatal Grok alias denies, composite inline Python
  workspace file reads, readonly inline Python probes, and the advisory
  foreground-server guard behavior.

## 0.1.28 - 2026-05-17

- Changed Grok PreToolUse blocks to return a non-error hook exit while still
  emitting `decision=deny`, so denied direct tool calls do not stop the whole
  Grok session.
- Kept inline Python workspace file IO blocked deterministically, including
  `open(...)`, `Path.read_text()`, `Path.read_bytes()`, globbing, and directory
  walks, so controller-side data inspection must route through taskctl.
- Updated block guidance and command contracts to say workspace reads, JSON/data
  processing, and product writes all require a bounded `taskctl.py capability`
  worker command.

## 0.1.27 - 2026-05-17

- Added assetgen task DB progress events for request start, Codex generation
  start/exit, per-image verification, manifest writes, completion, and failure.
- Added an assetgen heartbeat while the Codex image-generation subprocess is
  running so long multi-image generations keep `taskctl status` fresh instead
  of looking stuck until final completion.
- Assetgen progress writes refresh the running task `updated_at` timestamp to
  avoid stale-status false positives during long but healthy generations.

## 0.1.26 - 2026-05-17

- Added bounded service lifecycle instructions to every Codex worker prompt so
  dev servers and watchers must be started in the background, verified, and
  stopped before task completion.
- Added a `codex_exec.py` foreground dev-server watchdog that detects common
  ready/listening log patterns and terminates stuck foreground server runs
  after `CODEX_FOREGROUND_SERVER_GUARD_SECONDS` seconds, defaulting to 45.

## 0.1.25 - 2026-05-17

- Made Grok PreToolUse matching catch internal tool names such as `write` and
  `run_terminal_command` by routing all PreToolUse events through the guard.
- Normalized Windows MSYS-style Grok paths such as `/c/Users/...` before
  classifying hook write targets.
- Added lightweight project runtime auto-init for global installs so stable
  control-plane files can live once in the user install while mutable project
  state is created under the active workspace.
- Quoted installed hook command paths that contain shell metacharacters such as
  parentheses or ampersands, fixing Bash/Grok execution for paths like
  `ComfyUI-aki(1)`.
- Allowed read-only discovery commands to discard output with null-device
  redirection such as `2>/dev/null` or `>NUL` without being misclassified as
  shell file writes.
- Allowed read-only `python -c` probes used for hook/module discovery while
  still blocking inline Python file writes, filesystem mutations, subprocess
  launches, and mutating SQLite statements.
- Added a Windows `run_python.cmd` hook runner so installed Grok hooks can skip
  stale `TASKCTL_PYTHON` absolute paths and fall back to a current Python
  launcher with an actionable error if none exists.
- Added `taskctl.py --prompt-file` support for `capability`, `enqueue`, and
  `filter-input`, limited to workspace `.claude/task-plans/` and
  `.claude/artifacts/`, so Grok can pass long prompts without shell heredocs.
- Allowed Grok direct prompt-file writes under the active workspace's
  `.claude/task-plans/` even when hooks run from a global control plane.
- Added a safe in-place stale-project updater that preserves runtime artifacts
  while refreshing scripts, hooks, settings, environment, and version files.

## 0.1.24 - 2026-05-17

- Added Grok hook protocol compatibility for `toolName` / `toolInput` /
  `workspaceRoot` payloads.
- Changed Grok PreToolUse blocks to emit `decision=deny`, while preserving
  Claude's existing `decision=block` / `continue=false` output.
- Made Grok Stop focus checks return a non-blocking notice with exit code 0,
  avoiding misleading Stop hook exit code 2 reports.
- Fixed hook-generated route-token command syntax to use a shell-stable
  `--route-token=<token>` argument.

## 0.1.23 - 2026-05-16

- Fixed `test_hooks.py` so hook settings validation works in installed targets
  where installer rewrites hook commands to absolute local script paths.

## 0.1.22 - 2026-05-16

- Fixed Bash PreToolUse redirection detection so quoted prompt text containing
  Swift arrows (`->`), documentation blockquotes (`>`), or unsafe command
  examples is not mistaken for shell output redirection.
- Replaced regex-only output redirection detection with a shell-quote-aware
  scanner while continuing to block real file redirection such as `>file`,
  `1>file`, and `&> file`.
- Kept executable inline write guards for `python -c` and PowerShell mutation
  commands.

## 0.1.21 - 2026-05-16

- Added structured `state_input`, `state_output`, and `next_state` fields to
  every command catalog contract.
- Extended `taskctl command <name> --json` and text output so agents can see
  required input state, produced state, and the next recovery step without
  reading source code.
- Added regression coverage requiring every command contract to define nonempty
  state transition fields.

## 0.1.20 - 2026-05-16

- Added optional atom metadata fields to experiences:
  `atom_type`, `topics_json`, `skills_json`, `source_url_or_path`,
  `source_command`, `failure_signature`, `conflicts_with_json`, and
  `stale_reason`.
- Added backward-compatible experience table migration for existing local
  taskctl databases.
- Extended `experience-add` and `experience-list` to record and return atom
  metadata.
- Added `experience-sync-skill --min-confidence` so accepted low-confidence
  lessons can be hidden from generated learned-experience indexes.
- Added `experience-stale` for stale/conflicting evidence handling.
- Added regression coverage for old DB migration, atom metadata, low-confidence
  filtering, and stale conflict records.

## 0.1.19 - 2026-05-16

- Fixed installed `test_skill_manifest_check.py` so it imports the installed
  `.claude/scripts/skill_manifest_check.py` when the source-repository
  `tools/skill_manifest_check.py` wrapper is absent.

## 0.1.18 - 2026-05-16

- Fixed installed `test_all.py` so it runs the installed
  `.claude/scripts/skill_manifest_check.py` when the source-repository
  `tools/skill_manifest_check.py` wrapper is not present.
- Added an installed-target verification path for the skill manifest checker.

## 0.1.17 - 2026-05-16

- Added `.claude/skill-manifest.json` as the source of truth for bundled skill
  publication status, source paths, and Claude/plugin bridge paths.
- Added `tools/skill_manifest_check.py` to verify manifest coverage, bucket
  rules, deterministic bridge names, and byte-for-byte bridge/source
  consistency.
- Added `docs/SKILL_SOURCE_OF_TRUTH.md` documenting distributable, draft,
  deprecated, and private skill buckets.
- Added regression coverage for missing published manifest entries,
  non-distributable published skills, plugin bridge name drift, and current
  repository manifest health.
- Extended `test_all.py` to run the skill manifest checker and compile tool
  scripts.

## 0.1.16 - 2026-05-16

- Added a worker prompt soft contract for optional `CONTEXT.md` and
  `docs/adr/` sources so workers read them when present without blocking when
  absent.
- Added a docs-role template for explicitly requested project context and ADR
  documentation, with guardrails against creating those files as a side effect.
- Added `docs/PROJECT_CONTEXT.md` to document the optional glossary and ADR
  convention.
- Added installer regression coverage proving `CONTEXT.md` and `docs/adr/`
  are not created or copied during install.

## 0.1.15 - 2026-05-16

- Added `.claude/scripts/artifact_quality.py` with role-specific Markdown
  structure checks for `debugger`, `planner`, `uiux`, `reviewer`, and
  `closer` artifacts.
- Added `taskctl audit --quality`, which reports weak artifacts separately
  from missing artifact kinds or files while keeping default audit output
  backward-compatible.
- Added regression coverage proving structured debugger reports pass, weak
  generic reports fail quality checks, and quality failures do not masquerade
  as missing files.

## 0.1.14 - 2026-05-16

- Added resumable task checkpoints through `taskctl checkpoint-save`,
  `checkpoint-list`, `checkpoint-restore`, and `checkpoint-report`.
- Added command catalog contracts for checkpoint save/list/restore/report so
  Claude can recover failed or blocked work without guessing command syntax.
- Extended `taskctl audit` payloads with `next_role`, `next_artifacts`,
  `next_command`, and `resume_hint` for incomplete jobs.
- Added regression coverage for checkpoint persistence, restore, reports, and
  audit recovery hints.

## 0.1.13 - 2026-05-16

- Set Claude Code project permissions to `defaultMode=bypassPermissions` by
  default, so Claude's own permission prompt layer does not repeatedly block
  commands while project hooks remain the enforcement layer.
- Updated the installer to enforce the same permission mode in installed
  `.claude/settings.json`.
- Promoted critical taskctl roles (`planner`, `uiux`, `prototype`,
  `debugger`, `security`, `fullstack`, and `reviewer`) to `xhigh` reasoning,
  matching current OpenAI GPT model support.
- Stopped downgrading `xhigh` to `high` in the Codex CLI wrapper now that
  current Codex accepts `model_reasoning_effort="xhigh"`.

## 0.1.12 - 2026-05-16

- Changed PreToolUse block `next_command` to a directly executable
  `taskctl command <name>` catalog lookup, and moved the capability template to
  `replacement_command`.

## 0.1.11 - 2026-05-16

- Added a machine-readable command catalog through
  `.claude/scripts/command_catalog.py`.
- Added `taskctl command` and `taskctl doctor` so Claude can discover exact
  local commands without reading source or retrying guessed syntax.
- Added `next_command` and `command_contract` fields to PreToolUse block
  responses, pointing to the current machine's safe follow-up command.

## 0.1.10 - 2026-05-16

- Added focused non-implementation roles for `debugger`, `operator`,
  `security`, `docs`, and `release`, with routing schemas, role boundaries,
  model policy entries, and input-filter coverage.

## 0.1.9 - 2026-05-16

- Added a hybrid Bash write guard: deterministic direct-write patterns still
  block immediately, known-safe lifecycle commands pass, and ambiguous commands
  are reviewed by Codex `gpt-5.4-mini` with structured JSON output.
- Added Bash guard `.claude/.env` settings generated by the installer, plus
  tests that cover model-allowed and model-blocked ambiguous commands.
- Redacted common secret-like command arguments before sending ambiguous Bash
  commands to the model reviewer.

## 0.1.8 - 2026-05-16

- Allowed common JavaScript project lifecycle commands such as `npm install`,
  `npm run build`, `pnpm install`, `yarn test`, and `bun run build` through the
  Bash write guard.
- Kept direct shell file writes blocked for package-manager commands when they
  use output redirection or other explicit file-writing operators.

## 0.1.7 - 2026-05-16

- Ignored Claude background `<task-notification>` prompts in UserPromptSubmit
  so completed background commands cannot start or overwrite hard focus state.
- Added regression coverage to ensure background task notifications pass
  through without route injection or focus-state creation.

## 0.1.6 - 2026-05-16

- Fixed macOS global installs so PreToolUse allows versioned absolute Python
  executables such as `/Library/.../python3.13` when running installed
  control-plane scripts.
- Recovered prompt-template MCP installs when `image-2-prompt` completes the
  full payload install but its PowerShell launcher smoke fails on macOS.
- Changed worker artifact and experience command hints to use the current
  platform shell syntax instead of always showing PowerShell examples.
- Added regression coverage for the macOS absolute Python, MCP install
  recovery, and platform-specific worker command hints.

## 0.1.5 - 2026-05-16

- Changed worker-facing taskctl and focus_guard instructions to use the
  installed script path instead of target-project relative `.claude/scripts`
  paths.
- Added regression coverage for global installs where the target workspace has
  task state but no local control-plane scripts.
- Updated Claude CLI smoke guidance to exercise the same installed command path
  that hooks inject into production prompts.

## 0.1.4 - 2026-05-15

- Made the end-to-end test runner force UTF-8 subprocess I/O so Windows GitHub
  runners with non-UTF-8 default encodings do not fail before hook execution.
- Replaced a corrupted non-ASCII test prompt fixture with an ASCII fixture.

## 0.1.3 - 2026-05-15

- Normalized prompt-template MCP fingerprints against resolved install paths so
  cached readiness is stable on clean Windows runners.

## 0.1.2 - 2026-05-15

- Fixed Stop hook JSON output so blocking responses use only Claude-supported
  top-level fields.
- Removed unsupported `hookSpecificOutput` from Stop hook responses.
- Made Stop hook blocking output ASCII-safe to avoid mojibake in Windows hook
  transport.
- Normalized asset-generation workspace paths before containment checks so
  Windows runners report the intended raster-file validation errors.
- Refreshed repository presentation with structured README, architecture docs,
  operations docs, contribution guide, issue templates, and CI metadata.

## 0.1.1 - 2026-05-15

- Fixed installer hook rewriting so global/user-level installs use absolute
  installed hook script paths.
- Added regression coverage for hook script paths with normal and spaced Python
  executable paths.
- Verified real Claude CLI execution from a project without a local `.claude`
  directory.

## 0.1.0 - 2026-05-15

- Added SemVer release metadata through `VERSION` and `VERSIONING.md`.
- Added image prompt-template MCP installation, smoke checks, version tracking,
  latest-version warnings, and explicit upgrade flow.
- Added `assetgen` support for Codex-only raster asset generation with manifest
  output and prompt-template context.
- Added hard focus enforcement through `UserPromptSubmit` state and `Stop` hook
  blocking.
- Added installer coverage and end-to-end verification gates.
