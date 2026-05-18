# cc-router-codex Control Rules

This repository installs a Python/SQLite control plane for Claude Code and
Grok. Keep this file compact because Claude Code auto-loads it on session
startup; expanded policy lives in `.claude/MANDATORY_CONTEXT.md` and is enforced
by hooks plus `taskctl.py`.

## Production Path

- The controller model does not directly implement, test, review, or close
  production work.
- Run exactly one bounded capability at a time through the installed
  `taskctl.py` command shown by SessionStart or `taskctl.py command capability`.
- Generated capability commands must pass the active project as `--workspace`.
- Inspect `taskctl.py status` / `taskctl.py audit` after a capability returns
  before deciding the next capability.
- Direct product writes and ad hoc workspace data processing are blocked by
  hooks. Direct Claude writes are limited to runtime state under
  `.claude/artifacts/**`, `.claude/task-plans/**`, and scheduled task state.

## Roles

- `fullstack` is the only role that may modify product implementation code.
- `tester` writes reports, screenshots, and test files, not production source.
- `uiux`, `prototype`, `assetgen`, `planner`, `requirements`, `reviewer`,
  `closer`, `docs`, `release`, `operator`, and `security` stay within their
  bounded artifacts.
- Frontend work uses project design sources first. If no design source exists,
  route UI/UX/style selection before implementation, and use local raster assets
  with a recorded manifest when generated media is needed.

## Commands

Use command discovery instead of guessing syntax:

```bash
<installed-taskctl-command> command capability --workspace "<project>"
<installed-taskctl-command> doctor --workspace "<project>"
```

For lower-latency interactive sessions on Windows, launch through the installed
`.claude/scripts/claude_fast.cmd`. It keeps the configured default model, limits
Claude Code to the tools needed to call taskctl, disables slash-command loading,
and ignores unrelated MCP configs.

For production completion, rely on the capability audit result and then record
focus completion or exhaustion with the installed `focus_guard.py` command when
the Stop hook has an active goal.
