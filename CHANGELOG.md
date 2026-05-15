# Changelog

All notable changes to `cc-router-codex` are tracked here.

## Unreleased

- No changes yet.

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
