# cc-router-codex

Claude/Codex control plane with taskctl routing, role boundaries, and Codex-only raster asset generation.

## One-Line Install Into A Project

Windows PowerShell, from the target project directory:

```powershell
$p="$env:TEMP\cc-router-install.ps1"; irm https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.ps1 -OutFile $p; powershell -ExecutionPolicy Bypass -File $p
```

Linux/macOS, from the target project directory:

```sh
curl -fsSL https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.sh | sh
```

Both commands download this repository to a temporary directory, install into the current project, generate `.claude/.env` for the local machine, then delete the temporary copy.

If the target already has `.claude` or `CLAUDE.md`, the installer prints the paths that will be overwritten and continues only after the user types `y`.

For non-interactive overwrite:

```powershell
$p="$env:TEMP\cc-router-install.ps1"; irm https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.ps1 -OutFile $p; powershell -ExecutionPolicy Bypass -File $p -Yes
```

```sh
curl -fsSL https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.sh | sh -s -- -y
```

To install into an explicit target:

```powershell
$p="$env:TEMP\cc-router-install.ps1"; irm https://raw.githubusercontent.com/yxhpy/cc-router-codex/main/install.ps1 -OutFile $p; powershell -ExecutionPolicy Bypass -File $p -Target C:\path\to\project
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

For automation from a local clone:

```powershell
python C:\path\to\cc-router-codex\install.py --target C:\path\to\project -y
```
