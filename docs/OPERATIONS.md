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
| Hook tries to open `.claude/scripts/...` in the current project and fails | Hook command was generated with a relative script path | Reinstall with `v0.1.1` or newer so hooks use absolute installed script paths. |
| Claude final answer is blocked by `FOCUS_GUARD_BLOCK` | Active goal is not marked complete or exhausted | Run `focus_guard.py complete` with evidence, or `exhausted` after all viable attempts. |
| Image generation feels slow on the first run | `image-2-prompt` MCP is being installed and smoke-tested | Let the first install finish; later checks use cached readiness. |
| MCP warns that latest differs from installed | Local `image-2-prompt` commit is behind latest known remote | Review the warning and run the explicit upgrade command when desired. |
