# Changelog

All notable changes to `cc-router-codex` are tracked here.

## Unreleased

- No changes yet.

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
