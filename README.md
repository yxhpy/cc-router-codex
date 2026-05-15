# cc-router-codex

Claude/Codex control plane with taskctl routing, role boundaries, and Codex-only raster asset generation.

Current release: `v0.1.0`.

Production prompts are also protected by a hard focus guard. `UserPromptSubmit`
writes `.claude/task-plans/focus_state.json`; the `Stop` hook blocks final
answers until the controller records either:

```powershell
python .claude\scripts\focus_guard.py complete --workspace . --evidence "<artifacts/tests/result>"
```

or, only after all viable approaches are exhausted:

```powershell
python .claude\scripts\focus_guard.py exhausted --workspace . --evidence "<attempts and blockers>"
```

## One-Line Install Into A Project

Windows PowerShell, from the target project directory:

```powershell
$p="$env:TEMP\cc-router-install.ps1"; iwr https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.ps1 -OutFile $p; powershell -ExecutionPolicy Bypass -File $p
```

Linux/macOS, from the target project directory:

```sh
curl -fsSL https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.sh | sh
```

Both commands download this repository to a temporary directory, install into the current project, generate `.claude/.env` for the local machine, then delete the temporary copy.

If the target already has `.claude` or `CLAUDE.md`, the installer prints the paths that will be overwritten and continues only after the user types `y`.

For non-interactive overwrite:

```powershell
$p="$env:TEMP\cc-router-install.ps1"; iwr https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.ps1 -OutFile $p; powershell -ExecutionPolicy Bypass -File $p -Yes
```

```sh
curl -fsSL https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.sh | sh -s -- -y
```

To install into an explicit target:

```powershell
$p="$env:TEMP\cc-router-install.ps1"; iwr https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.ps1 -OutFile $p; powershell -ExecutionPolicy Bypass -File $p -Target C:\path\to\project
```

```sh
curl -fsSL https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.sh | sh -s -- --target /path/to/project
```

## Local Clone Install

From the target project directory, run the installer from a local clone:

```powershell
python C:\path\to\cc-router-codex\install.py
```

Or install into an explicit target:

```powershell
python C:\path\to\cc-router-codex\install.py --target C:\path\to\project
```

The installer copies `.claude` and `CLAUDE.md`, generates `.claude/.env` for the current machine, rewrites Claude hook commands to the detected Python executable, and excludes runtime state such as SQLite databases, logs, artifacts, task plans, and caches.

The generated `.claude/settings.json` also adds Bash allow rules for the
detected Python and Codex executables. This avoids a first-run Claude UI
approval prompt for the expected `taskctl.py capability` control-plane command
while the repo hook still blocks direct product writes and non-control-plane
shell commands.

Asset generation uses `.claude/scripts/assetgen_exec.py`. Before it asks Codex
to create raster files, it runs `.claude/scripts/prompt_template_mcp.py` to
fast-check the local `image-2-prompt` full-profile MCP under `.prompt-searcher`.
If the MCP is missing, the script installs it from
`https://github.com/yxhpy/image-2-prompt`, smoke-tests it, writes a cached ready
marker, searches prompt templates through MCP, and injects the retrieved
template context into the `gpt-5.4-mini` image-generation prompt. Later checks
use the cached marker and file fingerprint, so they stay on the fast path.
Versioning follows `VERSIONING.md`: the installed `image-2-prompt` MCP is
tracked by git commit SHA, compared against the latest known remote commit, and
warns when an upgrade is available. Upgrades are explicit and never happen
silently during generation.

Manual checks:

```powershell
python .claude\scripts\prompt_template_mcp.py check --workspace . --json
python .claude\scripts\prompt_template_mcp.py ensure --workspace . --json
python .claude\scripts\prompt_template_mcp.py version --workspace . --refresh --json
```

For automation from a local clone:

```powershell
python C:\path\to\cc-router-codex\install.py --target C:\path\to\project -y
```
