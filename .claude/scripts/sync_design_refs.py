#!/usr/bin/env python3
"""Sync DESIGN.md reference repositories used by frontend workers."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = SCRIPT_DIR.parent
DEFAULT_ROOT = CLAUDE_DIR / "design-references"

REFERENCE_REPOS = (
    {
        "name": "google-labs-code-design.md",
        "url": "https://github.com/google-labs-code/design.md",
        "path": "google-labs-code-design.md",
        "summary": "DESIGN.md format spec, examples, token schema, and lint/export tooling.",
    },
    {
        "name": "awesome-design-md",
        "url": "https://github.com/VoltAgent/awesome-design-md",
        "path": "awesome-design-md",
        "summary": "Curated DESIGN.md files grouped by product category, audience, and visual language.",
    },
)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def git_head(path: Path) -> str:
    result = run_git(["rev-parse", "HEAD"], cwd=path)
    return result.stdout.strip() if result.returncode == 0 else ""


def manifest_repo_path(root: Path, target: Path) -> str:
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(target)


def repo_design_files(target: Path) -> list[Path]:
    if not target.exists():
        return []
    return sorted(path for path in target.rglob("DESIGN.md") if ".git" not in path.parts)


def local_repo_status(root: Path, repo: dict[str, str]) -> dict[str, str]:
    target = root / repo["path"]
    head = git_head(target) if (target / ".git").exists() else ""
    if target.exists() and repo_design_files(target):
        action = "local"
        status = "using local cached reference"
    elif target.exists():
        action = "invalid"
        status = "local reference has no DESIGN.md files"
    else:
        action = "missing"
        status = "local reference is missing; rerun without --offline when network is available"
    return {
        "name": repo["name"],
        "url": repo["url"],
        "path": manifest_repo_path(root, target),
        "action": action,
        "status": status,
        "head_before": head,
        "head_after": head,
    }


def sync_repo(root: Path, repo: dict[str, str], depth: int) -> dict[str, str]:
    target = root / repo["path"]
    before = git_head(target) if (target / ".git").exists() else ""
    if not target.exists():
        result = run_git(["clone", "--depth", str(depth), repo["url"], str(target)])
        action = "cloned"
    elif (target / ".git").exists():
        result = run_git(["pull", "--ff-only"], cwd=target)
        action = "updated"
    else:
        return {
            "name": repo["name"],
            "url": repo["url"],
            "path": manifest_repo_path(root, target),
            "action": "failed",
            "status": "target exists but is not a git repository",
            "head_before": before,
            "head_after": "",
        }

    after = git_head(target)
    if result.returncode == 0 and not repo_design_files(target):
        status = "repository synced but no DESIGN.md files were found"
        action = "invalid"
    else:
        status = "ok" if result.returncode == 0 else (result.stderr.strip() or result.stdout.strip())
    return {
        "name": repo["name"],
        "url": repo["url"],
        "path": manifest_repo_path(root, target),
        "action": action if result.returncode == 0 else "failed",
        "status": status,
        "head_before": before,
        "head_after": after,
    }


def design_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    files = []
    for path in root.rglob("DESIGN.md"):
        if ".git" in path.parts:
            continue
        files.append(str(path.relative_to(root)).replace("\\", "/"))
    return sorted(files)


def write_manifest(root: Path, repos: list[dict[str, str]]) -> Path:
    manifest = {
        "updated_at": now(),
        "source": "sync_design_refs.py",
        "repos": repos,
        "design_files": design_files(root),
        "usage": {
            "project_design_first": "If the target project has DESIGN.md, design tokens, a style guide, theme files, or screenshots, those are authoritative.",
            "fallback_selection": "If no project design spec exists, frontend UI/UX workers must choose suitable DESIGN.md references from this directory based on domain, audience, product type, and interaction density.",
            "not_hardcoded": "Do not use one fixed visual style for all frontend jobs.",
        },
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clone or update frontend DESIGN.md reference repositories")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="target directory for references")
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-git", action="store_true", help="fail if git is unavailable")
    parser.add_argument("--offline", action="store_true", help="do not clone or pull; rebuild manifest from local files only")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    if args.offline:
        repos = [local_repo_status(root, repo) for repo in REFERENCE_REPOS]
    elif not shutil.which("git"):
        if args.require_git:
            raise SystemExit("ERROR: git is required to sync design references")
        repos = [
            {
                "name": repo["name"],
                "url": repo["url"],
                "path": manifest_repo_path(root, root / repo["path"]),
                "action": "skipped",
                "status": "git unavailable",
                "head_before": "",
                "head_after": "",
            }
            for repo in REFERENCE_REPOS
        ]
    else:
        repos = [sync_repo(root, repo, args.depth) for repo in REFERENCE_REPOS]

    manifest_path = write_manifest(root, repos)
    payload = {
        "root": str(root),
        "manifest": str(manifest_path),
        "repos": repos,
        "design_file_count": len(design_files(root)),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif not args.quiet:
        print(f"Design references synced: {payload['design_file_count']} DESIGN.md files")
        print(f"Manifest: {manifest_path}")
        for repo in repos:
            print(f"- {repo['name']}: {repo['action']} ({repo['status']})")

    failed = [repo for repo in repos if repo["action"] in {"failed", "missing", "invalid"}]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
