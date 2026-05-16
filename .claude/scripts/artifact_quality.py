#!/usr/bin/env python3
"""Role-specific quality checks for recorded task artifacts."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping


Check = tuple[str, tuple[str, ...]]

MARKDOWN_EXTENSIONS = {".md", ".markdown", ".txt"}

ROLE_MARKDOWN_CHECKS: dict[str, tuple[Check, ...]] = {
    "debugger": (
        ("feedback loop", ("feedback loop", "debug loop", "diagnosis loop")),
        ("reproduction", ("reproduction", "reproduce", "repro steps", "复现")),
        ("hypotheses", ("hypotheses", "hypothesis", "ranked hypotheses", "假设")),
        ("instrumentation", ("instrumentation", "probe result", "logs inspected", "evidence gathered", "排查证据")),
        ("fix recommendation", ("fix recommendation", "recommended fix", "remediation", "修复建议")),
        ("regression check", ("regression check", "regression test", "verification", "回归")),
    ),
    "planner": (
        ("scope", ("scope", "goal", "目标", "范围")),
        ("dependencies", ("dependencies", "dependency", "blocked by", "依赖")),
        ("vertical slices", ("vertical slices", "slice", "milestone", "阶段")),
        ("gates", ("gates", "acceptance", "exit criteria", "验收")),
        ("risks", ("risks", "risk", "tradeoff", "风险")),
    ),
    "uiux": (
        ("design source", ("design source", "reference", "inspiration", "设计来源")),
        ("style contract", ("style contract", "typography", "spacing", "color", "样式")),
        ("assets", ("assets", "image", "media", "素材")),
        ("states", ("states", "empty state", "loading", "error state", "状态")),
        ("responsive constraints", ("responsive", "breakpoint", "viewport", "适配")),
    ),
    "reviewer": (
        ("findings", ("findings", "finding", "问题")),
        ("severity", ("severity", "critical", "high", "medium", "low", "严重")),
        ("file references", ("file:", ".py:", ".ts:", ".tsx:", ".js:", ".go:", "line", "路径")),
        ("tests", ("tests", "test coverage", "verification", "测试")),
    ),
    "closer": (
        ("checklist", ("checklist", "acceptance", "requirements", "清单")),
        ("evidence", ("evidence", "verified", "verification", "证据")),
        ("coverage gaps", ("gaps", "missing", "uncovered", "残留")),
    ),
}


def normalize_text(value: str) -> str:
    lowered = value.lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", lowered)


def should_check_markdown(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in MARKDOWN_EXTENSIONS


def resolved_artifact_path(workspace: str, artifact: Mapping[str, Any]) -> Path:
    resolved = str(artifact.get("resolved_path") or "").strip()
    if resolved:
        return Path(resolved)
    path_value = str(artifact.get("path") or "").strip()
    path = Path(path_value)
    if path.is_absolute():
        return path
    return Path(workspace) / path


def missing_checks(text: str, checks: tuple[Check, ...]) -> list[str]:
    normalized = normalize_text(text)
    missing: list[str] = []
    for label, alternatives in checks:
        if not any(normalize_text(alternative) in normalized for alternative in alternatives):
            missing.append(label)
    return missing


def validate_artifacts(workspace: str, artifacts: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return quality issues for existing Markdown artifacts owned by supported roles."""
    issues: list[dict[str, Any]] = []
    for artifact in artifacts:
        role = str(artifact.get("role") or "").strip().lower()
        checks = ROLE_MARKDOWN_CHECKS.get(role)
        if not checks:
            continue
        path_value = str(artifact.get("path") or "").strip()
        if not path_value or not should_check_markdown(path_value):
            continue
        artifact_path = resolved_artifact_path(workspace, artifact)
        if not artifact_path.is_file():
            continue
        try:
            contents = artifact_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        missing = missing_checks(contents, checks)
        if not missing:
            continue
        issues.append(
            {
                "task_id": int(artifact["task_id"]) if str(artifact.get("task_id") or "").isdigit() else artifact.get("task_id"),
                "artifact_id": int(artifact["id"]) if str(artifact.get("id") or "").isdigit() else artifact.get("id"),
                "role": role,
                "kind": str(artifact.get("kind") or ""),
                "path": path_value,
                "resolved_path": str(artifact_path),
                "missing": missing,
                "summary": "missing required Markdown structure: " + ", ".join(missing),
            }
        )
    return issues
