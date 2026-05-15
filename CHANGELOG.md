# Changelog

All notable changes to `cc-router-codex` are tracked here.

## Unreleased

- Refresh repository presentation with structured README, architecture docs,
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
