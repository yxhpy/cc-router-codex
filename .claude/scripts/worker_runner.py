#!/usr/bin/env python3
"""Worker process launch and log interpretation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


FATAL_LOG_PATTERNS = (
    "CreateProcessWithLogonW failed",
    "failed: 1326",
    "windows sandbox error",
)


@dataclass(frozen=True)
class WorkerRunResult:
    output: str
    return_code: int
    log_path: str


def ensure_workspace(workspace: str) -> Path:
    path = Path(workspace).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise OSError(f"workspace is not a directory: {path}")
    return path


def parse_log_path(output: str) -> str:
    match = re.search(r"(?m)^LOG:\s*(.+)$", output)
    return match.group(1).strip() if match else ""


def worker_failure_reason(output: str, log_path: str = "") -> str:
    text = output or ""
    if log_path:
        path = Path(log_path)
        if path.exists():
            try:
                text += "\n" + path.read_text(encoding="utf-8", errors="replace")[-120000:]
            except OSError:
                pass
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
                return f"worker fatal pattern detected: {pattern}"
    return ""


def _timeout_text(exc: subprocess.TimeoutExpired) -> str:
    stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
    stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    return stdout + stderr


def run_worker_command(
    cmd: list[str],
    *,
    env: dict[str, str],
    workspace: str,
    timeout: int,
) -> WorkerRunResult:
    try:
        cwd = ensure_workspace(workspace)
    except OSError as exc:
        return WorkerRunResult(
            output=f"WORKER LAUNCH ERROR: {exc}",
            return_code=72,
            log_path="",
        )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            env=env,
            cwd=str(cwd),
            timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        output = _timeout_text(exc)
        output += f"\nTIMEOUT: worker exceeded {timeout} seconds"
        return_code = 124
    except OSError as exc:
        output = f"WORKER LAUNCH ERROR: {exc}"
        return_code = 72

    log_path = parse_log_path(output)
    fatal_reason = worker_failure_reason(output, log_path)
    if return_code == 0 and fatal_reason:
        output += "\n" + fatal_reason
        return_code = 70
    return WorkerRunResult(output=output, return_code=return_code, log_path=log_path)
