# Operations

This runbook covers the host-level tasks that should be repeatable after a
fresh clone or a global install.

## Install From GitHub

Windows PowerShell:

```powershell
$p="$env:TEMP\cc-router-install.ps1"; iwr https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.ps1 -OutFile $p; powershell -ExecutionPolicy Bypass -File $p
```

Linux/macOS:

```sh
curl -fsSL https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.sh | sh
```

For a user-level global install on Windows:

```powershell
python C:\path\to\cc-router-codex\install.py --target C:\Users\Administrator -y
```

## Verify An Install

From the installed target:

```powershell
python .claude\scripts\taskctl.py status
python .claude\scripts\focus_guard.py status --workspace . --json
python .claude\scripts\prompt_template_mcp.py check --workspace . --json
python .claude\scripts\taskctl.py audit <job_id> --quality --json
```

From the source repository:

```powershell
python -B .claude\scripts\test_all.py
```

Host-real smoke tests:

```powershell
python -B .claude\scripts\test_all.py --real-codex
python -B .claude\scripts\test_all.py --real-claude-cli
```

## Resume Failed Work

If a job fails, blocks, or Stop reports unfinished focus, checkpoint the current
state before retrying:

```powershell
python .claude\scripts\taskctl.py audit 1 --json
python .claude\scripts\taskctl.py audit 1 --quality --json
python .claude\scripts\taskctl.py checkpoint-save --job-id 1 --title "Resume blocked job"
python .claude\scripts\taskctl.py checkpoint-restore 1 --json
```

Use `checkpoint-list` when the checkpoint id is unknown, and
`checkpoint-report --job-id 1` before handoff or final closure when several
retry attempts were made. Use `audit --quality` when a job records
report-style Markdown artifacts and you need to distinguish a present but weak
report from a missing artifact.

## Command State Contracts

Inspect a command contract before running unfamiliar or previously failed
control-plane commands:

```powershell
python .claude\scripts\taskctl.py command capability --workspace . --json
python .claude\scripts\taskctl.py command checkpoint-restore --workspace .
```

Each contract includes:

- `state_input`: concrete state that must exist before running the command.
- `state_output`: state the command should produce or report.
- `next_state`: the success/failure move to run next.

Use these fields with `status`, `audit`, and checkpoints instead of reading
`taskctl.py` source or retrying guessed command shapes.

## Optional Project Context

`CONTEXT.md` and `docs/adr/` are optional project-owned docs. The installer does
not create them. When present, worker prompts ask Codex to read them before
choosing vocabulary, naming, or architecture decisions. Create or update them
only through an explicit docs capability requested by the user.

## Skill Manifest Check

Run the skill source-of-truth checker after adding, moving, drafting, or
deprecating bundled skills:

```powershell
python tools\skill_manifest_check.py
```

Installed targets can run the installed checker:

```powershell
python .claude\scripts\skill_manifest_check.py
```

The checker verifies `.claude/skill-manifest.json`, published skill coverage,
bucket rules, deterministic bridge paths, and plugin bridge/source content
consistency.

## Experience Atoms

Record high-signal reusable lessons with atom metadata when it helps later
search:

```powershell
python .claude\scripts\taskctl.py experience-add --task-id 1 --kind pitfall --title "Short title" --summary "What was learned" --atom-type pitfall --topic macos --skill skill-governance --source-command "command" --failure-signature "stable error text"
```

Hide accepted low-confidence lessons from generated skill indexes:

```powershell
python .claude\scripts\taskctl.py experience-sync-skill --min-confidence 4
```

Mark stale or conflicting lessons without deleting the historical row:

```powershell
python .claude\scripts\taskctl.py experience-stale 12 --reason "Contradicted by installed smoke" --conflicts-with 18
```

## Upgrade A Target

Re-run the installer against the same target:

```powershell
python C:\path\to\cc-router-codex\install.py --target C:\path\to\project -y
```

The installer replaces distributable control-plane files and regenerates local
`.claude/.env`. Runtime folders such as `.claude/artifacts`,
`.claude/task-plans`, and `.prompt-searcher` are not copied from the source
repository.

## Prompt Template MCP

Check local readiness:

```powershell
python .claude\scripts\prompt_template_mcp.py check --workspace . --json
```

Refresh latest-version knowledge without upgrading:

```powershell
python .claude\scripts\prompt_template_mcp.py version --workspace . --refresh --json
```

Upgrade explicitly:

```powershell
python .claude\scripts\prompt_template_mcp.py ensure --workspace . --refresh-version --upgrade --json
```

## Release Checklist

1. Update `VERSION`.
2. Update `README.md` and `CHANGELOG.md` if the release changes user-visible behavior.
3. Run `python -B .claude\scripts\test_all.py`.
4. Commit the release change.
5. Tag with `vX.Y.Z`, matching `VERSION`.
6. Push `main` and the tag.
7. Create a GitHub Release with verification notes.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Stop hook error: Hook JSON output validation failed` | Stop hook returned fields that Claude only accepts for other hook types | Reinstall with `v0.1.2` or newer, or update `.claude/scripts/hook_stop_focus.py`. |
| Hook tries to open `.claude/scripts/...` in the current project and fails | Hook command was generated with a relative script path | Reinstall with `v0.1.1` or newer so hooks use absolute installed script paths. |
| Claude final answer is blocked by `FOCUS_GUARD_BLOCK` | Active goal is not marked complete or exhausted | Run `focus_guard.py complete` with evidence, or `exhausted` after all viable attempts. |
| Claude keeps retrying the wrong taskctl command | Previous failure state was not restored | Run `taskctl checkpoint-list`, then `taskctl checkpoint-restore <id> --json` and follow the recorded `resume_hint`. |
| Prompt text with Swift `->` or Markdown `>` is blocked as shell redirection | Installed hook is older than `v0.1.22`, or the arrow was left unquoted in the shell command | Upgrade/reinstall; keep prompt text inside `--prompt "..."` so the shell does not treat `>` as syntax. |
| Image generation feels slow on the first run | `image-2-prompt` MCP is being installed and smoke-tested | Let the first install finish; later checks use cached readiness. |
| MCP warns that latest differs from installed | Local `image-2-prompt` commit is behind latest known remote | Review the warning and run the explicit upgrade command when desired. |
