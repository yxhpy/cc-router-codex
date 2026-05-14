#!/usr/bin/env python3
"""Validate main-model authored task inputs before they can reach Codex."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import sys
from typing import Any, Iterable

import llm_router
from safety_filter import check_prompt


ALLOWED_ROLES = {
    "planner",
    "divergent",
    "requirements",
    "prototype",
    "uiux",
    "fullstack",
    "tester",
    "reviewer",
    "closer",
}

ARTIFACT_KIND_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
CODE_EXTENSIONS = {
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".py",
    ".java",
    ".go",
    ".rs",
    ".php",
    ".rb",
    ".cs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".sql",
}
PRODUCT_CODE_KINDS = {"html", "css", "js", "javascript", "typescript", "code", "source", "implementation"}
NON_IMPLEMENTATION_ROLES = {"planner", "divergent", "requirements", "uiux", "prototype", "reviewer", "closer"}
FRONTEND_CODE_EXTENSIONS = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}
FRONTEND_ARTIFACT_KINDS = {"html", "css", "js", "javascript", "typescript", "frontend", "ui", "source", "code"}
DESIGN_SOURCE_MARKERS = (
    "style_contract",
    "design_reference_selection",
    ".claude/design-references",
    "DESIGN.md",
    "design tokens",
    "project design",
    "existing design",
    "existing style",
    "component library",
    "Storybook",
    "theme files",
    "asset_generation_brief",
    "local_asset_manifest",
    "generated bitmap",
    "local assets",
)


@dataclass
class TaskInputResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risk_score: int = 0
    convergence_score: float = 0.0


def _artifact_parts(artifact: str) -> tuple[str, str]:
    kind, _, path = artifact.partition(":")
    return kind.strip(), path.replace("\\", "/").strip()


def _is_valid_artifact_kind(kind: str) -> bool:
    return 1 <= len(kind) <= 80 and all(char in ARTIFACT_KIND_CHARS for char in kind)


def _is_valid_artifact_path(path: str) -> bool:
    return 1 <= len(path) <= 260 and not any(char in path for char in ("\x00", "\r", "\n"))


def _path_suffix(path: str) -> str:
    clean = path.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    name = clean.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    suffix = name[dot:]
    if len(suffix) < 2 or not suffix[1:].isalnum():
        return ""
    return suffix.lower()


def _is_test_path(path: str) -> bool:
    folded = path.replace("\\", "/").lower()
    return (
        "/test/" in folded
        or "/tests/" in folded
        or folded.startswith("test/")
        or folded.startswith("tests/")
        or ".test." in folded
        or ".spec." in folded
    )


def _is_product_code_artifact(kind: str, path: str) -> bool:
    lowered_kind = kind.lower()
    suffix = _path_suffix(path)
    if lowered_kind in PRODUCT_CODE_KINDS:
        return True
    if suffix in CODE_EXTENSIONS and not _is_test_path(path):
        return True
    return False


def _is_frontend_product_artifact(kind: str, path: str) -> bool:
    lowered_kind = kind.lower()
    suffix = _path_suffix(path)
    if lowered_kind in FRONTEND_ARTIFACT_KINDS and suffix in FRONTEND_CODE_EXTENSIONS:
        return not _is_test_path(path)
    if lowered_kind == "html":
        return True
    return suffix in FRONTEND_CODE_EXTENSIONS and not _is_test_path(path)


def _has_design_source_marker(text: str) -> bool:
    return any(marker in text for marker in DESIGN_SOURCE_MARKERS)


def _frontend_design_source_violations(role: str, title: str, prompt: str, artifacts: list[str]) -> list[str]:
    if role != "fullstack":
        return []
    frontend_artifacts = [
        artifact
        for artifact in artifacts
        if _is_frontend_product_artifact(*_artifact_parts(artifact))
    ]
    if not frontend_artifacts:
        return []
    combined = f"{title}\n{prompt}"
    if _has_design_source_marker(combined):
        return []
    joined = ", ".join(frontend_artifacts)
    return [
        "frontend fullstack capability requires a traceable design source before implementation "
        f"({joined}); run a uiux capability first to produce design_reference_selection and style_contract, "
        "or reference an existing project design source in the prompt"
    ]


def _artifact_role_violations(role: str, artifacts: list[str]) -> list[str]:
    violations: list[str] = []
    if role in NON_IMPLEMENTATION_ROLES:
        for artifact in artifacts:
            kind, path = _artifact_parts(artifact)
            if _is_product_code_artifact(kind, path):
                violations.append(
                    f"role {role} cannot produce product code artifact {kind}:{path or '<no path>'}; use fullstack"
                )

    if role == "tester":
        for artifact in artifacts:
            kind, path = _artifact_parts(artifact)
            suffix = _path_suffix(path)
            if suffix in CODE_EXTENSIONS and not _is_test_path(path):
                violations.append(f"role tester cannot modify product code artifact {kind}:{path}; use fullstack")
    return violations


def _llm_guard_result(role: str, title: str, prompt: str, artifacts: list[str]) -> llm_router.TaskInputGuard:
    return llm_router.guard_task_input(role=role, title=title, prompt=prompt, artifacts=artifacts)


def validate_task_input(
    role: str,
    title: str,
    prompt: str,
    required_artifacts: Iterable[str] | None = None,
) -> TaskInputResult:
    violations: list[str] = []
    warnings: list[str] = []
    artifacts = [str(item).strip() for item in (required_artifacts or []) if str(item).strip()]

    if role not in ALLOWED_ROLES:
        violations.append(f"unsupported role: {role}")
    if not title.strip():
        violations.append("missing task title")
    if len(title.strip()) > 180:
        violations.append("task title is too long")
    if len(prompt.strip()) < 20:
        violations.append("prompt is too short to be a bounded worker input")
    if len(prompt) > 120000:
        violations.append("prompt exceeds 120000 characters")
    for artifact in artifacts:
        kind, _, path = artifact.partition(":")
        if not _is_valid_artifact_kind(kind):
            violations.append(f"invalid required artifact kind: {kind or artifact}")
        if path:
            normalized = path.replace("\\", "/").strip()
            if not _is_valid_artifact_path(normalized):
                violations.append(f"invalid required artifact path for {kind}: {path}")
            if normalized.startswith("../") or "/../" in normalized:
                violations.append(f"artifact path escapes workspace for {kind}: {path}")

    violations.extend(_artifact_role_violations(role, artifacts))
    violations.extend(_frontend_design_source_violations(role, title, prompt, artifacts))
    if not violations and role in ALLOWED_ROLES:
        guard = _llm_guard_result(role, title, prompt, artifacts)
        if not guard.allowed:
            detail = guard.violation or "role boundary rejected by LLM task input guard"
            if guard.suggested_role:
                detail += f"; suggested role: {guard.suggested_role}"
            if guard.error:
                detail += f"; guard error: {guard.error}"
            violations.append(detail)
        if not guard.has_action:
            warnings.append("LLM guard found no explicit action; make the atomic operation more concrete")
        if not guard.bounded:
            violations.append("LLM guard found the prompt is not one bounded capability step")

    safety = check_prompt(prompt, require_anchor=False, deep=True)
    violations.extend(safety.violations)
    if safety.convergence_score < 0.25:
        warnings.append("low convergence score; prompt may be too broad for one atomic operation")

    return TaskInputResult(
        passed=not violations,
        violations=violations,
        warnings=warnings,
        risk_score=safety.risk_score,
        convergence_score=safety.convergence_score,
    )


def normalize_required_artifacts(values: Iterable[Any] | None) -> list[str]:
    return [str(item).strip() for item in (values or []) if str(item).strip()]


def require_valid_task_input(
    role: str,
    title: str,
    prompt: str,
    required_artifacts: Iterable[str] | None = None,
) -> TaskInputResult:
    result = validate_task_input(role, title, prompt, required_artifacts)
    if not result.passed:
        details = "; ".join(result.violations)
        raise SystemExit(f"ERROR: task input rejected by filter: {details}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a taskctl worker input before capability execution")
    parser.add_argument("--role", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--prompt")
    parser.add_argument("--required-artifact", action="append")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    prompt = args.prompt
    if prompt is None and not sys.stdin.isatty():
        prompt = sys.stdin.read()
    prompt = prompt or ""

    result = validate_task_input(args.role, args.title, prompt, args.required_artifact)
    if args.json:
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    else:
        print("PASS" if result.passed else "FAIL")
        for violation in result.violations:
            print(f"  violation: {violation}")
        for warning in result.warnings:
            print(f"  warning: {warning}")
        print(f"Risk score: {result.risk_score}/100")
        print(f"Convergence: {result.convergence_score:.2f}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
