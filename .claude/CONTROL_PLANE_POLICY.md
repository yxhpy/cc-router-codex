# Claude Control-Plane Policy

This repository treats `.claude` as a local control plane, not as a free-write
scratch directory.

## Local Resources

- Design references are local under `.claude/design-references`.
- `.claude/design-references/manifest.json` must use relative paths so the
  checkout is portable across machines.
- Use `python .claude/scripts/sync_design_refs.py --offline --quiet` to rebuild
  the manifest from cached files without network access.
- Use `python .claude/scripts/sync_design_refs.py --quiet` only when refreshing
  from upstream is intended.
- Missing visual materials can be localized by generated bitmap assets. The
  control plane should capture prompts and constraints in an
  `asset_generation_brief`, then the `assetgen` role should create or place the
  resulting files under a local project asset directory and record a
  `local_asset_manifest`. Fast assetgen may skip prompt-template retrieval, but
  it still must verify real local raster outputs.

## File Decomposition

- New control-plane logic should go into focused modules under
  `.claude/scripts/`, not into `taskctl.py` by default.
- A file over 300 lines needs a clear single responsibility. If it mixes
  persistence, command execution, policy, rendering, and tests, split the next
  change into a module first.
- A file over 500 lines is legacy/transition territory. Do not add another
  independent responsibility to it without first extracting a helper module.
- Current split points:
  - `project_paths.py`: repository root and path normalization.
  - `claude_write_policy.py`: direct-write allow/deny rules.
  - `sync_design_refs.py`: local design-reference sync and manifest writing.

## Root Execution

- Scripts must resolve the repository root from `__file__`, not from the
  shell's current directory.
- Hook script paths may point at this control-plane repository from any Claude
  session, but generated task commands must pass `--workspace <target-project>`.
- Worker subprocesses must run with `cwd` set to the stored job workspace.
- Logs default to `<workspace>/logs/codex` so changing directories does not
  scatter logs.
- Do not prepend `cd /d` to suggested Bash commands. `cd /d` is `cmd.exe`
  syntax and fails under Bash; run the absolute Python script path directly.

## Claude Direct Writes

Claude controller direct writes are intentionally narrow:

- Allowed by default:
  - `.claude/artifacts/**`
  - `.claude/task-plans/**`
  - `.claude/scheduled_tasks.json`
- Blocked by default:
  - product files anywhere outside the runtime paths above
  - `.claude/settings.json`
  - `.claude/model_policy.json`
  - `.claude/MANDATORY_CONTEXT.md`
  - `.claude/scripts/**`
  - `.claude/skills/**`
  - `.claude/plugins/**`
  - `.claude/design-references/**` direct writes
- Refresh `.claude/design-references/**` only through `sync_design_refs.py`.
- Change control-plane source/config only during explicit maintenance by
  setting `CLAUDE_CONTROL_PLANE_WRITE=1` before launching Claude, or by creating
  `.claude/ALLOW_CONTROL_PLANE_WRITES` outside the controller flow with a future
  UTC expiry such as `{"expires_at":"2026-05-14T15:30:00Z"}`. Expired, empty,
  or malformed marker files do not allow writes. Delete the marker after
  maintenance.
- Product implementation remains Codex-owned through:

```bash
python <control-plane>/.claude/scripts/taskctl.py capability --role fullstack --title "<title>" --prompt "<bounded worker prompt>" --artifact <kind:path> --workspace "<target-project>" --goal "<user goal>"
```
