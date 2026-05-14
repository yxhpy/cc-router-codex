#!/usr/bin/env python3
"""
codex_log.py — Progressive log viewer for Codex execution logs.

Displays codex logs incrementally to avoid flooding the context window.
The main model reads logs in chunks, only when needed.

Usage:
  python codex_log.py "<log-file>"                   first 40 lines (default)
  python codex_log.py "<log-file>" head <N>          first N lines
  python codex_log.py "<log-file>" tail <N>          last N lines
  python codex_log.py "<log-file>" range <S> <E>     lines S through E
  python codex_log.py "<log-file>" errors            error/exception lines only
  python codex_log.py "<log-file>" summary           stats + key metadata + errors
  python codex_log.py --list [N]                     list recent N log files
"""

import argparse
import os
import re
import sys
from pathlib import Path

from project_paths import REPO_ROOT


ERROR_PATTERN = re.compile(r'error|fail|exception|traceback|panic|fatal', re.IGNORECASE)
META_PATTERN = re.compile(r'^(===|Started|Finished|Workspace|Sandbox|Exit code|Duration|Prompt len)')


def list_logs(log_dir: Path, count: int):
    """List recent log files."""
    if not log_dir.exists():
        print(f"No logs found in {log_dir}")
        return

    logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        print(f"No .log files in {log_dir}")
        return

    print(f"=== Recent Codex Logs ({log_dir}) ===")
    for log in logs[:count]:
        size = log.stat().st_size
        mtime = log.stat().st_mtime
        import datetime
        ts = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {ts}  {size:>8} bytes  {log.name}")


def read_log_lines(log_file: Path):
    """Read all lines from log file."""
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def cmd_head(lines, n: int):
    """Show first N lines."""
    if n >= len(lines):
        for line in lines:
            sys.stdout.write(line)
    else:
        for line in lines[:n]:
            sys.stdout.write(line)
        print()
        print(f"--- Showing {n} of {len(lines)} lines ---")
        print(f"    Next: codex_log.py \"{LOG_PATH}\" range {n+1} {n*2}")


def cmd_tail(lines, n: int):
    """Show last N lines."""
    for line in lines[-n:]:
        sys.stdout.write(line)
    print()
    print(f"--- Showing last {n} of {len(lines)} lines ---")


def cmd_range(lines, start: int, end: int):
    """Show lines start through end (1-indexed)."""
    for line in lines[start-1:end]:
        sys.stdout.write(line)
    print()
    print(f"--- Lines {start}-{end} of {len(lines)} ---")
    if end < len(lines):
        print(f"    Next: codex_log.py \"{LOG_PATH}\" range {end+1} {end+40}")


def cmd_errors(lines):
    """Show only error lines."""
    print(f"=== Errors in log ===")
    found = False
    for i, line in enumerate(lines, 1):
        if ERROR_PATTERN.search(line):
            sys.stdout.write(f"  L{i}: {line}")
            found = True
    if not found:
        print("(no errors found)")
    print()
    print(f"--- Error lines from {len(lines)} total ---")


def cmd_summary(log_file: Path, lines):
    """Show stats + metadata + error summary."""
    total_bytes = log_file.stat().st_size
    print("=== Log Summary ===")
    print(f"File:       {log_file}")
    print(f"Size:       {total_bytes} bytes")
    print(f"Lines:      {len(lines)}")
    print()

    # Extract key metadata
    for line in lines:
        if META_PATTERN.match(line):
            sys.stdout.write(f"  {line.rstrip()}\n")

    # Error count
    error_lines = [(i, line) for i, line in enumerate(lines, 1) if ERROR_PATTERN.search(line)]
    if error_lines:
        print(f"\n--- Errors ({len(error_lines)}) ---")
        for i, line in error_lines[:20]:
            sys.stdout.write(f"  L{i}: {line}")
        if len(error_lines) > 20:
            print(f"  ... and {len(error_lines) - 20} more errors")
    else:
        print("\nStatus: No errors detected.")
    print()
    print("--- Use 'head'/'tail'/'range' to read full log ---")


# Global for displaying next-step hints
LOG_PATH = ""


def main():
    global LOG_PATH

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Progressive log viewer for Codex execution logs",
        add_help=False,
    )
    parser.add_argument("target", nargs="?", help="Log file path, or --list")
    parser.add_argument("mode", nargs="?", default="head",
                        choices=["head", "tail", "range", "errors", "summary"])
    parser.add_argument("arg1", nargs="?", type=int, default=40, help="N lines, or range start")
    parser.add_argument("arg2", nargs="?", type=int, default=None, help="Range end")
    parser.add_argument("--list", action="store_true", help="List recent log files")
    parser.add_argument("-h", "--help", action="store_true", help="Show help")
    args = parser.parse_args()

    # Help
    if args.help or (not args.target and not args.list):
        print("Usage:")
        print("  python codex_log.py \"<log-file>\"                  first 40 lines")
        print("  python codex_log.py \"<log-file>\" head <N>         first N lines")
        print("  python codex_log.py \"<log-file>\" tail <N>         last N lines")
        print("  python codex_log.py \"<log-file>\" range <S> <E>    lines S through E")
        print("  python codex_log.py \"<log-file>\" errors           error lines only")
        print("  python codex_log.py \"<log-file>\" summary          stats + errors")
        print("  python codex_log.py --list [N]                  list recent N logs")
        sys.exit(0)

    # List mode
    if args.list or args.target == "--list":
        log_dir = Path(os.environ.get("CODEX_LOG_DIR", str(REPO_ROOT / "logs" / "codex")))
        count = args.arg1 if args.arg1 else 10
        list_logs(log_dir, count)
        return

    # Validate log file
    log_file = Path(args.target)
    if not log_file.exists():
        print(f"ERROR: Log file not found: {log_file}")
        print("Use 'python codex_log.py --list' to see available logs.")
        sys.exit(1)

    LOG_PATH = str(log_file)
    lines = read_log_lines(log_file)

    if not lines:
        print("(empty log file)")
        return

    # Dispatch
    mode = args.mode
    if mode == "head":
        cmd_head(lines, args.arg1)
    elif mode == "tail":
        cmd_tail(lines, args.arg1)
    elif mode == "range":
        end = args.arg2 if args.arg2 is not None else args.arg1 + 40
        cmd_range(lines, args.arg1, end)
    elif mode == "errors":
        cmd_errors(lines)
    elif mode == "summary":
        cmd_summary(log_file, lines)


if __name__ == "__main__":
    main()
