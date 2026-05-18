# Interactive Speed Validation

Use this checklist after any change that affects Claude/Grok submission speed,
SessionStart context, launchers, model policy, or installed hook behavior.

## Required Validation

1. Run the source test suite from the repository root:

```powershell
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" ".claude\scripts\test_all.py"
```

Expected result: `ALL CHECKS PASSED`.

2. Install the current source into both global targets:

```powershell
& "C:\Users\Administrator\.claude\runtime\python-3.11.9\python.exe" install.py --target "C:\Users\Administrator" -y
& "C:\Users\Administrator\.claude\runtime\python-3.11.9\python.exe" install.py --target "C:\Users\Administrator\.grok" -y
```

3. Re-enable local interactive fast knobs after install rewrites `.env`:

```text
TASKCTL_SESSION_CONTEXT_PROFILE=compact
TASKCTL_INTERACTIVE_SPEED_PROFILE=fast
TASKCTL_INTERACTIVE_ASYNC=1
```

4. Verify installed versions from the installed entrypoints:

```powershell
& "C:\Users\Administrator\.claude\scripts\run_python.cmd" "C:\Users\Administrator\.claude\scripts\taskctl.py" doctor --workspace "C:\Users\Administrator\Desktop\demo04"
& "C:\Users\Administrator\.grok\.claude\scripts\run_python.cmd" "C:\Users\Administrator\.grok\.claude\scripts\taskctl.py" doctor --workspace "C:\Users\Administrator\Desktop\grok-router-smoke"
```

Expected result: both report the release version being validated.

5. Verify fast model policy:

```powershell
& "C:\Users\Administrator\.claude\scripts\run_python.cmd" "C:\Users\Administrator\.claude\scripts\model_policy.py" general fullstack --speed-profile fast --json
```

Expected result: `source` is `fast:fullstack`, model is downshifted, and effort
is lower than the quality profile.

6. Verify compact memory files stayed compact:

```powershell
(Get-Content "C:\Users\Administrator\CLAUDE.md" | Measure-Object -Character -Line)
(Get-Content "C:\Users\Administrator\.claude\CLAUDE.md" | Measure-Object -Character -Line)
```

Expected result: both are compact. They must not regress to the old expanded
11KB control-rule file.

7. Verify the balanced fast launcher with real Claude Code:

```powershell
& "C:\Users\Administrator\.claude\scripts\claude_fast.cmd" -p "只回复 OK。" --output-format stream-json --include-hook-events --max-budget-usd 0.08 --no-session-persistence --verbose
```

Expected proof in the stream:

- result text is `OK`
- `tools` is exactly `Bash`, `Edit`, `Glob`, `Grep`, `Read`, `Write`
- `mcp_servers` is empty
- `slash_commands` is empty
- `input_tokens` is close to the known slim baseline, about 13k on this machine

8. Verify the strict submit launcher with real Claude Code:

```powershell
& "C:\Users\Administrator\.claude\scripts\claude_submit.cmd" -p "只回复 OK。" --output-format stream-json --include-hook-events --max-budget-usd 0.06 --no-session-persistence --verbose
```

Expected proof in the stream:

- result text is `OK`
- `tools` is exactly `Bash`
- `mcp_servers` is empty
- `slash_commands` is empty
- `input_tokens` is close to the known submit baseline, about 10.5k on this machine

## Interpretation

Do not call a speed change verified just because Claude returns `OK`. The
verification must inspect the stream-json `init` payload and model usage. The
important evidence is reduced loaded tools/MCP/slash-command context and a lower
`input_tokens` count from the installed launcher, not only wall-clock time.

For taskctl submit/status/audit turns, use `claude_submit.cmd`. For turns that
need lightweight controller file access, use `claude_fast.cmd`. Use the default
`claude` command only when unrelated MCP tools, slash commands, or full Claude
Code tooling are intentionally needed.
