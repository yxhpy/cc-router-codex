#!/usr/bin/env python3
"""Verify bundled skill source-of-truth and bridge consistency."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable


MANIFEST_PATH = Path(".claude") / "skill-manifest.json"
VALID_BUCKETS = {"distributable", "draft", "deprecated", "private"}


@dataclass
class CheckResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def rel_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def manifest_path(root: Path) -> Path:
    return root / MANIFEST_PATH


def load_manifest(root: Path) -> tuple[dict[str, Any], list[str]]:
    path = manifest_path(root)
    if not path.is_file():
        return {}, [f"missing skill manifest: {MANIFEST_PATH.as_posix()}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [f"invalid skill manifest JSON: {exc}"]
    if not isinstance(payload, dict):
        return {}, ["skill manifest must be a JSON object"]
    return payload, []


def safe_relative_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def read_skill_name(skill_dir: Path) -> str:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return ""
    lines = skill_md.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith("name:"):
            return stripped.split(":", 1)[1].strip().strip("'\"")
    return ""


def iter_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*") if path.is_file())


def directories_match(source: Path, bridge: Path) -> bool:
    source_files = {path.relative_to(source).as_posix(): path for path in iter_files(source)}
    bridge_files = {path.relative_to(bridge).as_posix(): path for path in iter_files(bridge)}
    if set(source_files) != set(bridge_files):
        return False
    for name, source_path in source_files.items():
        if source_path.read_bytes() != bridge_files[name].read_bytes():
            return False
    return True


def is_published_path(path: Path) -> bool:
    parts = path.parts
    if len(parts) >= 3 and parts[0] == ".claude" and parts[1] == "skills":
        return True
    if len(parts) >= 5 and parts[0] == ".claude" and parts[1] == "plugins" and parts[3] == "skills":
        return True
    return False


def published_skill_dirs(root: Path) -> list[Path]:
    dirs: list[Path] = []
    skills_dir = root / ".claude" / "skills"
    if skills_dir.is_dir():
        dirs.extend(path for path in skills_dir.iterdir() if (path / "SKILL.md").is_file())
    plugins_dir = root / ".claude" / "plugins"
    if plugins_dir.is_dir():
        for plugin in plugins_dir.iterdir():
            plugin_skills = plugin / "skills"
            if plugin_skills.is_dir():
                dirs.extend(path for path in plugin_skills.iterdir() if (path / "SKILL.md").is_file())
    return sorted(dirs)


def deterministic_bridge_path(entry_name: str, bridge: dict[str, Any]) -> str:
    surface = str(bridge.get("surface") or "").strip()
    if surface == "claude":
        return f".claude/skills/{entry_name}"
    if surface == "codex-plugin":
        plugin = str(bridge.get("plugin") or "").strip()
        if not plugin:
            return ""
        return f".claude/plugins/{plugin}/skills/{entry_name}"
    return ""


def validate_entry(root: Path, entry: dict[str, Any], errors: list[str]) -> None:
    name = str(entry.get("name") or "").strip()
    bucket = str(entry.get("bucket") or "").strip()
    if not name:
        errors.append("skill entry missing name")
        return
    if bucket not in VALID_BUCKETS:
        errors.append(f"invalid bucket for {name}: {bucket or '<missing>'}")
        return
    source_rel = safe_relative_path(entry.get("source"))
    if source_rel is None:
        errors.append(f"invalid source path for {name}: {entry.get('source')!r}")
        return
    source_dir = root / source_rel
    if not (source_dir / "SKILL.md").is_file():
        errors.append(f"missing skill source for {name}: {source_rel.as_posix()}")
        return
    source_name = read_skill_name(source_dir)
    if source_name != name:
        errors.append(f"source name mismatch: {name} source has {source_name or '<missing>'}")
    bridges = entry.get("bridges") or []
    if not isinstance(bridges, list):
        errors.append(f"bridges must be a list for {name}")
        return
    if bucket != "distributable":
        if bridges or is_published_path(source_rel):
            errors.append(f"non-distributable skill is published: {name}")
        return
    if not bridges:
        errors.append(f"distributable skill has no bridges: {name}")
        return
    for bridge in bridges:
        if not isinstance(bridge, dict):
            errors.append(f"invalid bridge object for {name}")
            continue
        bridge_rel = safe_relative_path(bridge.get("path"))
        if bridge_rel is None:
            errors.append(f"invalid bridge path for {name}: {bridge.get('path')!r}")
            continue
        expected = deterministic_bridge_path(name, bridge)
        if not expected:
            errors.append(f"invalid bridge surface for {name}: {bridge.get('surface')!r}")
        elif bridge_rel.as_posix() != expected:
            errors.append(f"bridge path is not deterministic for {name}: {bridge_rel.as_posix()} != {expected}")
        bridge_dir = root / bridge_rel
        if not (bridge_dir / "SKILL.md").is_file():
            errors.append(f"missing bridge skill for {name}: {bridge_rel.as_posix()}")
            continue
        bridge_name = read_skill_name(bridge_dir)
        if bridge_name != name:
            errors.append(f"bridge name mismatch: {name} bridge {bridge_rel.as_posix()} has {bridge_name or '<missing>'}")
        if source_dir.resolve() != bridge_dir.resolve() and not directories_match(source_dir, bridge_dir):
            errors.append(f"bridge differs from source: {name} {bridge_rel.as_posix()}")


def check_repository(root: str | Path) -> CheckResult:
    root_path = Path(root).expanduser().resolve()
    payload, errors = load_manifest(root_path)
    if errors:
        return CheckResult(ok=False, errors=errors)
    skills = payload.get("skills")
    if not isinstance(skills, list):
        return CheckResult(ok=False, errors=["skill manifest field 'skills' must be a list"])

    entries: dict[str, dict[str, Any]] = {}
    bridge_paths: dict[str, str] = {}
    for entry in skills:
        if not isinstance(entry, dict):
            errors.append("skill entry must be an object")
            continue
        name = str(entry.get("name") or "").strip()
        if name in entries:
            errors.append(f"duplicate skill entry: {name}")
            continue
        entries[name] = entry
        validate_entry(root_path, entry, errors)
        if str(entry.get("bucket") or "").strip() == "distributable":
            for bridge in entry.get("bridges") or []:
                if isinstance(bridge, dict) and (bridge_rel := safe_relative_path(bridge.get("path"))):
                    bridge_paths[bridge_rel.as_posix()] = name

    for skill_dir in published_skill_dirs(root_path):
        relative = rel_path(skill_dir, root_path)
        skill_name = read_skill_name(skill_dir) or skill_dir.name
        entry = entries.get(skill_name)
        if entry is None:
            errors.append(f"missing manifest entry: {skill_name} at {relative}")
            continue
        if str(entry.get("bucket") or "").strip() != "distributable":
            errors.append(f"non-distributable skill is published: {skill_name} at {relative}")
            continue
        if relative not in bridge_paths:
            errors.append(f"published bridge not listed for {skill_name}: {relative}")

    return CheckResult(ok=not errors, errors=errors)


def print_result(result: CheckResult) -> None:
    if result.ok:
        print("Skill manifest check: PASS")
    else:
        print("Skill manifest check: FAIL")
        for error in result.errors:
            print(f"  - {error}")
    for warning in result.warnings:
        print(f"  warning: {warning}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify skill manifest and bridge consistency.")
    parser.add_argument("--root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = check_repository(Path(args.root))
    if args.json:
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    else:
        print_result(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
