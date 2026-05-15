#!/usr/bin/env python3
"""
codex_exec.py — Codex CLI wrapper with mandatory log capture.

All codex output is forced to a timestamped log file.
Stdout returns only SUCCESS or ERROR summary — never raw codex output.
The main model sees only the result; logs are read incrementally on demand.

Usage:
  python .claude/scripts/codex_exec.py [-w <workspace>] [-m <mode>] "<prompt>"
  python .claude/scripts/codex_exec.py -w /tmp/ws "write a script that..."

Sandbox modes (default: danger-full-access):
  -m sandbox   → danger-full-access
  -m workspace → workspace-write
  -m readonly  → read-only

Output:
  stdout : SUCCESS or ERROR (exit=N) + log path
  log    : logs/codex-YYYYMMDD_HHMMSS.log

Env vars:
  CODEX_LOG_DIR → override log directory (default: ./logs/codex)
  CODEX_VERBOSE → if "1", also show codex output in real time (debug only)
  CODEX_PROXY -> optional single proxy URL copied to HTTP(S)/ALL proxy vars
  CODEX_MODEL -> optional Codex model override
  CODEX_REASONING_EFFORT -> optional Codex CLI reasoning effort override
  --reasoning-effort -> per-run reasoning effort override
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

# ── Local safety filter: check prompt BEFORE sending to codex ──
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from project_paths import REPO_ROOT, script_command
from safety_filter import check_prompt as _safety_check


PROXY_PAIRS = (
    ("HTTP_PROXY", "http_proxy"),
    ("HTTPS_PROXY", "https_proxy"),
    ("ALL_PROXY", "all_proxy"),
    ("NO_PROXY", "no_proxy"),
)
FATAL_LOG_PATTERNS = (
    "CreateProcessWithLogonW failed",
    "failed: 1326",
    "windows sandbox error",
)


def _find_codex() -> str:
    """Find the codex CLI binary. Checks PATH, common install locations, and OS-specific paths."""
    configured = os.environ.get("CODEX_BIN", "").strip()
    if configured:
        return configured

    # Prefer PATH resolution. On Windows this finds .cmd shims reliably, while
    # subprocess.run(["codex", ...]) may miss them depending on PATHEXT handling.
    for name in ("codex", "codex.cmd"):
        resolved = shutil.which(name)
        if resolved:
            try:
                result = subprocess.run([resolved, "--version"], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    return resolved
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    # Windows: check npm global install path
    if sys.platform == "win32":
        candidates = [
            # npm global
            os.path.expandvars(r"%APPDATA%\\npm\\codex.cmd"),
            os.path.expandvars(r"%APPDATA%\\npm\\codex"),
            # npx
            os.path.expandvars(r"%APPDATA%\\npm\\codex.ps1"),
            # Local relative
            "codex.cmd",
        ]
    else:
        candidates = [
            # npm global (linux/mac)
            os.path.expanduser("~/.npm-global/bin/codex"),
            "/usr/local/bin/codex",
            "/usr/bin/codex",
        ]

    candidates.insert(0, "codex")  # Retry bare as fallback

    for candidate in candidates:
        try:
            result = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Last resort: try shell invocation
    return "codex"


def _fatal_log_reason(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        folded = stripped.lower()
        is_runtime_error = (
            (stripped[:4].isdigit() and " error codex_core::exec" in folded)
            or stripped.startswith("execution error:")
        )
        if not is_runtime_error:
            continue
        for pattern in FATAL_LOG_PATTERNS:
            if pattern.lower() in folded:
                return f"fatal worker log pattern detected: {pattern}"
    return ""


def _first_env(env: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return ""


def _mirror_proxy_var(env: dict[str, str], upper: str, lower: str) -> None:
    value = _first_env(env, (upper, lower))
    if not value:
        return
    env.setdefault(upper, value)
    env.setdefault(lower, value)


def _apply_proxy_env(env: dict[str, str]) -> list[str]:
    """Normalize proxy variables for the Codex child process.

    Existing HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY values are inherited and
    mirrored between upper/lowercase names. CODEX_PROXY is an optional compact
    override for environments that want one proxy URL for all outbound traffic.
    """
    codex_proxy = env.get("CODEX_PROXY") or env.get("codex_proxy")
    if codex_proxy:
        for upper, lower in PROXY_PAIRS:
            if upper == "NO_PROXY":
                continue
            if env.get(upper) or env.get(lower):
                continue
            env.setdefault(upper, codex_proxy)
            env.setdefault(lower, codex_proxy)

    for upper, lower in PROXY_PAIRS:
        _mirror_proxy_var(env, upper, lower)

    return sorted(
        name
        for pair in PROXY_PAIRS
        for name in pair
        if env.get(name)
    )


def _build_child_env() -> tuple[dict[str, str], list[str]]:
    child_env = os.environ.copy()
    child_env.setdefault("PSExecutionPolicyPreference", "Bypass")
    child_env.setdefault("POWERSHELL_TELEMETRY_OPTOUT", "1")
    proxy_names = _apply_proxy_env(child_env)
    return child_env, proxy_names


def _codex_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"


def _read_config_reasoning_effort() -> str:
    path = _codex_config_path()
    if not path.exists():
        return ""
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except Exception:
        return ""
    value = config.get("model_reasoning_effort", "")
    return str(value).strip() if value else ""


def _normalize_reasoning_effort(value: str) -> tuple[str, str]:
    normalized = value.strip().lower()
    if normalized == "xhigh":
        return "high", "xhigh is not accepted by this Codex CLI; using high"
    if normalized in {"minimal", "low", "medium", "high"}:
        return normalized, ""
    return "", f"unsupported reasoning effort ignored: {value}"


def _reasoning_effort_override(env: dict[str, str], explicit_value: str = "") -> tuple[str, str]:
    explicit = explicit_value.strip() or env.get("CODEX_REASONING_EFFORT", "").strip()
    if explicit:
        return _normalize_reasoning_effort(explicit)

    configured = _read_config_reasoning_effort()
    if configured:
        effort, note = _normalize_reasoning_effort(configured)
        if note:
            return effort, "config " + note
    return "", ""


def _resolve_sandbox_mode(mode: str) -> tuple[str, str]:
    sandbox_map = {
        "sandbox": "danger-full-access",
        "workspace": "workspace-write",
        "readonly": "read-only",
    }
    sandbox_mode = sandbox_map[mode]
    if sys.platform == "win32" and sandbox_mode == "workspace-write":
        return (
            "danger-full-access",
            "workspace-write is not reliable with this Windows Codex CLI sandbox; using danger-full-access",
        )
    return sandbox_mode, ""


def _build_codex_command(
    codex_bin: str,
    sandbox_mode: str,
    workspace: str = "",
    model_override: str = "",
    reasoning_effort: str = "",
) -> list[str]:
    cmd = [
        codex_bin,
        "exec",
        "--sandbox",
        sandbox_mode,
        "--skip-git-repo-check",
    ]
    if model_override:
        cmd.extend(["--model", model_override])
    if workspace:
        cmd.extend(["-C", workspace])
    if reasoning_effort:
        cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    # Read instructions from stdin. Passing multi-line prompts as argv can be
    # truncated by shell/CLI shims on Windows, leaving workers without their brief.
    cmd.append("-")
    return cmd


def _default_log_dir(workspace: str | None) -> Path:
    if os.environ.get("CODEX_LOG_DIR"):
        return Path(os.environ["CODEX_LOG_DIR"])
    base = Path(workspace).expanduser().resolve() if workspace else REPO_ROOT
    return base / "logs" / "codex"


def _codex_stdio_kwargs(log_file_handle):
    return {
        "stdout": log_file_handle,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Codex CLI wrapper with mandatory log capture",
        add_help=False,
    )
    parser.add_argument("prompt", nargs="?", help="The prompt to send to codex")
    parser.add_argument("-w", "--workspace", default=None, help="Working directory for codex")
    parser.add_argument("--model", default=None, help="Optional Codex model override")
    parser.add_argument("--reasoning-effort", default="", help="Optional per-run reasoning effort override")
    parser.add_argument("-m", "--mode", default="sandbox",
                        choices=["sandbox", "workspace", "readonly"],
                        help="Sandbox mode (default: sandbox=danger-full-access)")
    parser.add_argument("--skip-safety", action="store_true", help="Bypass local safety filter (NOT recommended)")
    parser.add_argument("-h", "--help", action="store_true", help="Show help")
    args = parser.parse_args()

    if args.help or not args.prompt:
        print("Usage: python codex_exec.py [-w <workspace>] [-m <mode>] \"<prompt>\"")
        print()
        print("Sandbox modes:")
        print("  sandbox   → --sandbox danger-full-access (default)")
        print("  workspace → --sandbox workspace-write")
        print("  readonly  → --sandbox read-only")
        print()
        print("Env vars:")
        print("  CODEX_LOG_DIR → override log directory")
        print("  CODEX_VERBOSE=1 → show codex output in real time")
        print("  CODEX_PROXY → optional proxy URL for Codex child process")
        print("  CODEX_MODEL → optional model override")
        print("  CODEX_REASONING_EFFORT → optional reasoning effort override")
        print("  --reasoning-effort → per-run reasoning effort override")
        sys.exit(0 if args.help else 1)

    # Fix stdout encoding for Windows
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    sandbox_mode, sandbox_note = _resolve_sandbox_mode(args.mode)

    # Auto-create workspace directory
    if args.workspace:
        ws_path = Path(args.workspace)
        ws_path.mkdir(parents=True, exist_ok=True)

    # ── Pre-flight safety check: validate prompt BEFORE sending to codex ──
    if not args.skip_safety:
        safety_result = _safety_check(args.prompt, require_anchor=False, deep=True)
        if not safety_result.passed:
            print("SAFETY BLOCK: Prompt rejected by local safety filter")
            for v in safety_result.violations:
                print(f"  → {v}")
            print(f"Risk score: {safety_result.risk_score}/100")
            if safety_result.suggestions:
                print("Suggestions:")
                for s in safety_result.suggestions:
                    print(f"  → {s}")
            print()
            print("Use --skip-safety to bypass (NOT recommended)")
            print(f"Or submit the goal through: {script_command('taskctl.py')} submit-auto \"<goal>\"")
            sys.exit(2)
        # Warn on high risk but still pass
        if safety_result.risk_score >= 40:
            print(f"[safety: RISK={safety_result.risk_score}/100, convergence={safety_result.convergence_score:.2f}]")

    # Setup log directory
    log_dir = _default_log_dir(args.workspace)
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"codex-{timestamp}.log"

    # Locate codex binary
    codex_bin = _find_codex()

    model_override = args.model or os.environ.get("CODEX_MODEL")

    child_env, proxy_names = _build_child_env()
    reasoning_effort, reasoning_note = _reasoning_effort_override(child_env, args.reasoning_effort)
    cmd = _build_codex_command(
        codex_bin=codex_bin,
        sandbox_mode=sandbox_mode,
        workspace=args.workspace or "",
        model_override=model_override or "",
        reasoning_effort=reasoning_effort,
    )

    # Write log header
    start_ts = time.time()
    with open(log_file, "w", encoding="utf-8", errors="replace") as f:
        f.write("=== codex exec ===\n")
        f.write(f"Started:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Workspace:  {args.workspace or '(none/cwd)'}\n")
        f.write(f"Sandbox:    {sandbox_mode}\n")
        if sandbox_note:
            f.write(f"Sandbox note: {sandbox_note}\n")
        f.write(f"Model:      {model_override or '(config default)'}\n")
        f.write(f"Prompt len: {len(args.prompt)} chars\n")
        f.write(f"Proxy env:  {', '.join(proxy_names) if proxy_names else '(none)'}\n")
        if reasoning_effort:
            f.write(f"Reasoning:  override {reasoning_effort}\n")
        if reasoning_note:
            f.write(f"Reasoning note: {reasoning_note}\n")
        f.write(f"Log file:   {log_file}\n")
        f.write("=" * 60 + "\n\n")

    # Execute codex
    exit_code = 0
    verbose = os.environ.get("CODEX_VERBOSE") == "1"

    try:
        with open(log_file, "a", encoding="utf-8", errors="replace") as log_f:
            # Write directly to the log file instead of capturing pipes. Long-running
            # browser/dev-server descendants can inherit stdout handles; with PIPE,
            # Python may wait forever for EOF after Codex itself has finished.
            result = subprocess.run(
                cmd,
                input=args.prompt,
                env=child_env,
                **_codex_stdio_kwargs(log_f),
            )
            exit_code = result.returncode
            if verbose:
                log_f.flush()
                try:
                    sys.stderr.write(log_file.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
    except FileNotFoundError:
        with open(log_file, "a", encoding="utf-8") as log_f:
            log_f.write("\nFATAL: codex CLI not found in PATH\n")
        print("ERROR: codex CLI not found in PATH")
        print(f"LOG: {log_file}")
        sys.exit(1)
    except Exception as e:
        with open(log_file, "a", encoding="utf-8") as log_f:
            log_f.write(f"\nFATAL: {e}\n")
        print(f"ERROR: {e}")
        print(f"LOG: {log_file}")
        sys.exit(1)

    fatal_reason = ""
    try:
        fatal_reason = _fatal_log_reason(log_file.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        fatal_reason = ""
    if exit_code == 0 and fatal_reason:
        exit_code = 70
        with open(log_file, "a", encoding="utf-8", errors="replace") as log_f:
            log_f.write(f"\nFATAL: {fatal_reason}\n")

    duration = time.time() - start_ts

    # Write log footer
    with open(log_file, "a", encoding="utf-8", errors="replace") as f:
        f.write("\n" + "=" * 60 + "\n")
        f.write(f"Finished:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Duration:   {duration:.1f}s\n")
        f.write(f"Exit code:  {exit_code}\n")

    # Count log lines
    log_lines = 0
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        log_lines = sum(1 for _ in f)

    # Output result to stdout
    if exit_code == 0:
        print(f"SUCCESS ({duration:.1f}s, {log_lines} lines logged)")
    else:
        print(f"ERROR exit={exit_code} ({duration:.1f}s, {log_lines} lines logged)")
        # Show tail error context (last 5 non-empty lines)
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.rstrip() for line in f if line.strip()]
            for line in lines[-5:]:
                safe_line = line[:200].encode("ascii", errors="replace").decode("ascii")
                print(f"  | {safe_line}")

    print(f"LOG: {log_file}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
