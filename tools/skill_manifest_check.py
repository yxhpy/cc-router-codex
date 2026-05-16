#!/usr/bin/env python3
"""Repository entrypoint for the installed skill manifest checker."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / ".claude" / "scripts" / "skill_manifest_check.py"
SPEC = importlib.util.spec_from_file_location("_installed_skill_manifest_check", CHECKER_PATH)
checker = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)

CheckResult = checker.CheckResult
check_repository = checker.check_repository


def main() -> int:
    return int(checker.main())


if __name__ == "__main__":
    raise SystemExit(main())
