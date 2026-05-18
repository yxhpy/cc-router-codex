#!/usr/bin/env python3
"""
Content safety filter for Codex prompts.

Task decomposition is intentionally not implemented here. Production work
belongs to one Codex capability worker at a time, coordinated by taskctl.py and
persisted in SQLite. Legacy decompose/pipeline CLI modes are disabled and point
to taskctl.py capability.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from dataclasses import dataclass, field

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from project_paths import script_command


INSTANT_BLOCK = (
    "jailbreak",
    "ignore safety",
    "disable filter",
    "bypass content filter",
    "malware",
    "ransomware",
    "keylogger",
    "phishing",
    "botnet",
    "ddos",
    "credential theft",
)

HIGH_RISK = (
    "exploit",
    "shellcode",
    "privilege escalation",
    "steal password",
    "dump password",
    "extract credentials",
    "remote code execution",
)

ANCHORS = (
    "This is for a developer productivity tool.",
    "This is part of routine software maintenance.",
    "This is for internal code review and testing.",
)


@dataclass
class SafetyResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    risk_score: int = 0
    suggestions: list[str] = field(default_factory=list)
    convergence_score: float = 0.0


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _contains(text: str, needle: str) -> bool:
    haystack = " ".join(text.split())
    target = " ".join(needle.split())
    start = 0
    while True:
        index = haystack.find(target, start)
        if index < 0:
            return False
        before_ok = index == 0 or not _is_word_char(haystack[index - 1])
        after_index = index + len(target)
        after_ok = after_index >= len(haystack) or not _is_word_char(haystack[after_index])
        if before_ok and after_ok:
            return True
        start = index + 1


def _word_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    for char in text:
        if _is_word_char(char):
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _score_convergence(prompt: str) -> float:
    words = _word_tokens(prompt)
    if not words:
        return 0.0
    score = 0.35
    if len(words) <= 120:
        score += 0.2
    if any(token in prompt for token in (".", "/", "\\", "test", "file", "function", "class", "command")):
        score += 0.2
    if len(words) > 300:
        score -= 0.25
    return max(0.0, min(1.0, score))


def check_prompt(prompt: str, require_anchor: bool = False, deep: bool = True) -> SafetyResult:
    lower = prompt.lower()
    violations: list[str] = []
    suggestions: list[str] = []
    risk = 0

    for term in INSTANT_BLOCK:
        if _contains(lower, term):
            violations.append(f"blocked term: {term}")
            risk += 40

    for term in HIGH_RISK:
        if _contains(lower, term):
            risk += 20
            if deep:
                violations.append(f"high-risk term: {term}")

    if require_anchor and not any(anchor.lower() in lower for anchor in ANCHORS):
        violations.append("missing benign task anchor")
        suggestions.append("Route the task through taskctl.py with explicit acceptance criteria.")

    if violations and not suggestions:
        suggestions.append("Rewrite the prompt as a bounded, transparent software task.")

    return SafetyResult(
        passed=not violations,
        violations=violations,
        risk_score=min(100, risk),
        suggestions=suggestions,
        convergence_score=_score_convergence(prompt),
    )


def beautify(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip().lower()
        if (
            stripped.startswith("sure")
            or stripped.startswith("certainly")
            or stripped.startswith("of course")
            or stripped.startswith("here is")
            or stripped.startswith("here's")
            or "as an ai" in stripped
        ):
            continue
        lines.append(line.rstrip())
    compacted: list[str] = []
    blank_count = 0
    for line in lines:
        if line:
            compacted.append(line)
            blank_count = 0
            continue
        blank_count += 1
        if blank_count <= 1:
            compacted.append(line)
    return "\n".join(compacted).strip()


def print_rules() -> None:
    print("=== Instant Block Terms ===")
    for term in INSTANT_BLOCK:
        print(f"  - {term}")
    print("\n=== High Risk Terms ===")
    for term in HIGH_RISK:
        print(f"  - {term}")
    print("\nLegacy decomposition is disabled.")
    print(f'Use: {script_command("taskctl.py")} capability --role <role> --title "<title>" --prompt "<bounded worker prompt>" --artifact <kind:path> --workspace "<workspace>" --goal "<goal>"')


def disabled_decomposition() -> None:
    print("ERROR: legacy local decomposition is disabled.")
    print("Use the SQLite control plane instead:")
    print(f'  {script_command("taskctl.py")} capability --role <role> --title "<title>" --prompt "<bounded worker prompt>" --artifact <kind:path> --workspace "<workspace>" --goal "<goal>"')
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Codex prompt safety filter", add_help=False)
    parser.add_argument("mode", nargs="?", default="check", choices=["check", "decompose", "pipeline", "beautify", "converge"])
    parser.add_argument("text", nargs="?")
    parser.add_argument("--rules", action="store_true")
    parser.add_argument("--anchors", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-anchor", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args = parser.parse_args(argv)

    if args.help:
        print(__doc__)
        return 0
    if args.rules:
        print_rules()
        return 0
    if args.anchors:
        for index, anchor in enumerate(ANCHORS):
            print(f"[{index}] {anchor}")
        return 0

    text = args.text
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        print('ERROR: No input text provided. Use: python safety_filter.py check "<prompt>"')
        return 1

    if args.mode in ("decompose", "pipeline"):
        disabled_decomposition()

    if args.mode == "beautify":
        print(beautify(text))
        return 0

    if args.mode == "converge":
        result = check_prompt(text, require_anchor=False, deep=False)
        print(f"Convergence score: {result.convergence_score:.2f}")
        print(f"Risk score: {result.risk_score}/100")
        if result.convergence_score < 0.4:
            print("Verdict: DIVERGENT - narrow the prompt before running one taskctl capability.")
        elif result.convergence_score < 0.7:
            print("Verdict: MODERATE - choose one bounded capability role and artifact.")
        else:
            print("Verdict: CONVERGENT - bounded enough for a worker task.")
        return 0

    result = check_prompt(text, require_anchor=not args.no_anchor, deep=True)
    if args.json:
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    else:
        print("PASS" if result.passed else "FAIL")
        for violation in result.violations:
            print(f"  - {violation}")
        for suggestion in result.suggestions:
            print(f"  suggestion: {suggestion}")
        print(f"Risk score: {result.risk_score}/100")
        print(f"Convergence: {result.convergence_score:.2f}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
