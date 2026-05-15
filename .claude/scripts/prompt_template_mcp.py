#!/usr/bin/env python3
"""Fast bootstrap and MCP retrieval for image prompt templates.

The assetgen path uses this module before image generation:
1. Do a cached local readiness check for image-2-prompt.
2. Install the full profile only when the local MCP server is missing.
3. Query the MCP server for reusable prompt templates.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Any, Mapping, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

DEFAULT_REPO = "https://github.com/yxhpy/image-2-prompt"
DEFAULT_REF = "main"
DEFAULT_TARGET = ".prompt-searcher"
READY_FILE = ".cc-router-mcp-ready.json"
VERSION_FILE = ".cc-router-mcp-version.json"
LATEST_FILE = ".cc-router-mcp-latest.json"
PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "cc-router-codex-assetgen", "version": "1"}
REQUIRED_RELATIVE_FILES = (
    "install.json",
    "data/search-docs.json",
    "data/search-index.json",
    "data/search-facets.json",
    "src/prompt_searcher/mcp_server.py",
)


class PromptTemplateMcpError(RuntimeError):
    """Raised when prompt template MCP setup or retrieval fails."""


@dataclass(frozen=True)
class PromptMcpVersion:
    repo: str
    ref: str
    installed_commit: str
    latest_commit: str
    latest_source: str
    checked_at: str
    is_latest: bool
    warning: str = ""

    @property
    def upgrade_available(self) -> bool:
        return bool(self.installed_commit and self.latest_commit and self.installed_commit != self.latest_commit)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "ref": self.ref,
            "installed_commit": self.installed_commit,
            "latest_commit": self.latest_commit,
            "latest_source": self.latest_source,
            "checked_at": self.checked_at,
            "is_latest": self.is_latest,
            "upgrade_available": self.upgrade_available,
            "warning": self.warning,
        }


@dataclass(frozen=True)
class PromptMcpStatus:
    ok: bool
    installed: bool
    fast: bool
    target: str
    reason: str
    elapsed_ms: float
    version: PromptMcpVersion | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "installed": self.installed,
            "fast": self.fast,
            "target": self.target,
            "reason": self.reason,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }
        if self.version is not None:
            payload["version"] = self.version.as_dict()
            payload["upgrade_available"] = self.version.upgrade_available
        return payload


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolve_workspace(value: str | Path | None = None) -> Path:
    return Path(value or os.getcwd()).expanduser().resolve()


def resolve_target(workspace: str | Path | None = None, target: str | Path | None = None) -> Path:
    raw = target or os.environ.get("ASSETGEN_PROMPT_MCP_TARGET") or DEFAULT_TARGET
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = resolve_workspace(workspace) / path
    return path.resolve()


def read_json(path: Path) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise PromptTemplateMcpError(f"JSON payload is not an object: {path}")
    return payload


def configured_repo() -> str:
    return os.environ.get("ASSETGEN_PROMPT_MCP_REPO") or DEFAULT_REPO


def configured_ref() -> str:
    return os.environ.get("ASSETGEN_PROMPT_MCP_REF") or DEFAULT_REF


def git_command() -> str:
    git_bin = shutil.which("git")
    if not git_bin:
        raise PromptTemplateMcpError("git is required for image-2-prompt version checks")
    return git_bin


def run_git(args: Sequence[str], *, cwd: Path | None = None, timeout: float | None = None) -> str:
    completed = subprocess.run(
        [git_command(), *args],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout or float(os.environ.get("ASSETGEN_PROMPT_MCP_VERSION_TIMEOUT", "5")),
        check=False,
    )
    if completed.returncode != 0:
        raise PromptTemplateMcpError(completed.stderr.strip() or completed.stdout.strip() or "git command failed")
    return completed.stdout.strip()


def remote_latest_commit(repo: str, ref: str) -> str:
    output = run_git(["ls-remote", repo, ref])
    first = output.splitlines()[0].split()[0] if output.splitlines() else ""
    if not first:
        raise PromptTemplateMcpError(f"cannot resolve latest commit for {repo}@{ref}")
    return first


def local_clone_commit(path: Path) -> str:
    return run_git(["rev-parse", "HEAD"], cwd=path)


def read_version_file(target: Path) -> Mapping[str, Any] | None:
    path = target / VERSION_FILE
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError, PromptTemplateMcpError):
        return None


def read_latest_cache(target: Path) -> Mapping[str, Any] | None:
    path = target / LATEST_FILE
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError, PromptTemplateMcpError):
        return None


def write_latest_cache(target: Path, *, repo: str, ref: str, latest_commit: str, source: str = "remote") -> None:
    payload = {
        "schemaVersion": 1,
        "repo": repo,
        "ref": ref,
        "latestCommit": latest_commit,
        "source": source,
        "checkedAt": utc_now(),
    }
    (target / LATEST_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_version_file(
    target: Path,
    *,
    repo: str,
    ref: str,
    installed_commit: str,
    latest_commit: str = "",
) -> None:
    payload = {
        "schemaVersion": 1,
        "standard": "image-2-prompt git commit SHA",
        "repo": repo,
        "ref": ref,
        "installedCommit": installed_commit,
        "installedAt": utc_now(),
        "latestCommitAtInstall": latest_commit or installed_commit,
    }
    (target / VERSION_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest_cache_is_fresh(cache: Mapping[str, Any] | None) -> bool:
    if not cache:
        return False
    checked = str(cache.get("checkedAt") or "")
    if not checked:
        return False
    try:
        checked_at = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    except ValueError:
        return False
    ttl = int(os.environ.get("ASSETGEN_PROMPT_MCP_LATEST_TTL_SECONDS", "3600"))
    age = datetime.now(timezone.utc) - checked_at
    return age.total_seconds() <= max(0, ttl)


def version_status(target: Path, *, refresh_latest: bool = False) -> PromptMcpVersion:
    target.mkdir(parents=True, exist_ok=True)
    repo = configured_repo()
    ref = configured_ref()
    version = read_version_file(target) or {}
    installed_commit = str(version.get("installedCommit") or "").strip()
    cache = read_latest_cache(target)
    latest_commit = ""
    latest_source = "unknown"
    warning = ""

    if (
        not refresh_latest
        and cache
        and cache.get("repo") == repo
        and cache.get("ref") == ref
        and latest_cache_is_fresh(cache)
    ):
        latest_commit = str(cache.get("latestCommit") or "").strip()
        latest_source = "cache"
    else:
        try:
            latest_commit = remote_latest_commit(repo, ref)
            latest_source = "remote"
            write_latest_cache(target, repo=repo, ref=ref, latest_commit=latest_commit, source=latest_source)
        except (OSError, subprocess.SubprocessError, PromptTemplateMcpError) as exc:
            if cache and cache.get("repo") == repo and cache.get("ref") == ref:
                latest_commit = str(cache.get("latestCommit") or "").strip()
                latest_source = "stale-cache"
                warning = f"latest version check failed, using cached latest: {exc}"
            else:
                latest_commit = ""
                latest_source = "unavailable"
                warning = f"latest version check failed: {exc}"

    is_latest = bool(installed_commit and latest_commit and installed_commit == latest_commit)
    if installed_commit and latest_commit and installed_commit != latest_commit:
        warning = f"upgrade available for image-2-prompt: installed {installed_commit[:12]}, latest {latest_commit[:12]}"
    elif not installed_commit:
        warning = "installed image-2-prompt version is unknown; run ensure --upgrade to reinstall at the latest commit"
    return PromptMcpVersion(
        repo=repo,
        ref=ref,
        installed_commit=installed_commit,
        latest_commit=latest_commit,
        latest_source=latest_source,
        checked_at=utc_now(),
        is_latest=is_latest,
        warning=warning,
    )


def _venv_python(target: Path) -> Path:
    install_record = target / "install.json"
    if install_record.is_file():
        try:
            python_value = str(read_json(install_record).get("python") or "").strip()
        except (OSError, json.JSONDecodeError, PromptTemplateMcpError):
            python_value = ""
        if python_value:
            return Path(python_value)
    if os.name == "nt":
        return target / ".venv" / "Scripts" / "python.exe"
    return target / ".venv" / "bin" / "python"


def mcp_command(target: Path) -> list[str]:
    return [
        str(_venv_python(target)),
        str(target / "src" / "prompt_searcher" / "mcp_server.py"),
        "--index-dir",
        str(target / "data"),
    ]


def required_paths(target: Path) -> list[Path]:
    return [target / item for item in REQUIRED_RELATIVE_FILES] + [_venv_python(target)]


def install_fingerprint(target: Path) -> dict[str, dict[str, int | str]] | None:
    root = target.expanduser().resolve()
    result: dict[str, dict[str, int | str]] = {}
    for path in required_paths(root):
        if not path.is_file():
            return None
        resolved = path.expanduser().resolve()
        try:
            key = resolved.relative_to(root).as_posix()
        except ValueError:
            key = str(resolved)
        stat = resolved.stat()
        result[key] = {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
    return result


def status_reason(base: str, version: PromptMcpVersion | None = None) -> str:
    if version and version.warning:
        return f"{base}; {version.warning}"
    return base


def _status(
    start: float,
    *,
    ok: bool,
    installed: bool,
    fast: bool,
    target: Path,
    reason: str,
    version: PromptMcpVersion | None = None,
) -> PromptMcpStatus:
    return PromptMcpStatus(
        ok=ok,
        installed=installed,
        fast=fast,
        target=str(target),
        reason=status_reason(reason, version),
        elapsed_ms=(perf_counter() - start) * 1000,
        version=version,
    )


def quick_check(
    workspace: str | Path | None = None,
    target: str | Path | None = None,
    *,
    refresh_version: bool = False,
) -> PromptMcpStatus:
    start = perf_counter()
    resolved = resolve_target(workspace, target)
    ready = resolved / READY_FILE
    if not ready.is_file():
        return _status(start, ok=False, installed=False, fast=True, target=resolved, reason="ready marker missing")
    try:
        marker = read_json(ready)
    except (OSError, json.JSONDecodeError, PromptTemplateMcpError) as exc:
        return _status(start, ok=False, installed=False, fast=True, target=resolved, reason=f"invalid ready marker: {exc}")
    fingerprint = install_fingerprint(resolved)
    if fingerprint is None:
        return _status(start, ok=False, installed=False, fast=True, target=resolved, reason="required MCP files missing")
    if marker.get("schemaVersion") != 1 or marker.get("ok") is not True:
        return _status(start, ok=False, installed=True, fast=True, target=resolved, reason="ready marker is not ok")
    if marker.get("fingerprint") != fingerprint:
        return _status(start, ok=False, installed=True, fast=True, target=resolved, reason="install fingerprint changed")
    version = version_status(resolved, refresh_latest=refresh_version)
    return _status(start, ok=True, installed=True, fast=True, target=resolved, reason="cached MCP readiness", version=version)


def installed_profile_ok(target: Path) -> tuple[bool, str]:
    fingerprint = install_fingerprint(target)
    if fingerprint is None:
        return False, "required MCP files missing"
    try:
        record = read_json(target / "install.json")
    except (OSError, json.JSONDecodeError, PromptTemplateMcpError) as exc:
        return False, f"install record unreadable: {exc}"
    if record.get("profile") != "full":
        return False, "image-2-prompt full profile is required for MCP"
    return True, "installed full profile"


def _json_line_messages(messages: Sequence[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)


def _parse_mcp_response_lines(stdout: str) -> dict[int, Mapping[str, Any]]:
    responses: dict[int, Mapping[str, Any]] = {}
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping) and isinstance(payload.get("id"), int):
            responses[int(payload["id"])] = payload
    return responses


def _tool_payload(response: Mapping[str, Any]) -> Mapping[str, Any]:
    if "error" in response:
        raise PromptTemplateMcpError(f"MCP error: {response['error']}")
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise PromptTemplateMcpError("MCP response is missing result object")
    structured = result.get("structuredContent")
    if isinstance(structured, Mapping):
        return structured
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, Mapping):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                text = item["text"].strip()
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise PromptTemplateMcpError(f"MCP text payload is not JSON: {exc}") from exc
                if isinstance(parsed, Mapping):
                    return parsed
    raise PromptTemplateMcpError("MCP tool response did not contain JSON content")


def mcp_call_tools(
    target: Path,
    calls: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    timeout: float | None = None,
) -> list[Mapping[str, Any]]:
    timeout = timeout or float(os.environ.get("ASSETGEN_PROMPT_MCP_TIMEOUT", "8"))
    attempts = max(1, int(os.environ.get("ASSETGEN_PROMPT_MCP_RETRIES", "2")))

    def once() -> list[Mapping[str, Any]]:
        messages: list[Mapping[str, Any]] = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        ]
        for offset, (name, arguments) in enumerate(calls, start=2):
            messages.append(
                {
                    "jsonrpc": "2.0",
                    "id": offset,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": dict(arguments)},
                }
            )

        completed = subprocess.run(
            mcp_command(target),
            input=_json_line_messages(messages),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise PromptTemplateMcpError(
                "image-2-prompt MCP failed with exit %s: %s"
                % (completed.returncode, completed.stderr.strip() or completed.stdout[:1000])
            )
        responses = _parse_mcp_response_lines(completed.stdout)
        payloads: list[Mapping[str, Any]] = []
        for response_id in range(2, 2 + len(calls)):
            response = responses.get(response_id)
            if response is None:
                raise PromptTemplateMcpError(
                    "MCP response %s was not returned; stdout=%r stderr=%r"
                    % (response_id, completed.stdout[:500], completed.stderr[:500])
                )
            payloads.append(_tool_payload(response))
        return payloads

    errors: list[str] = []
    for _attempt in range(attempts):
        try:
            return once()
        except PromptTemplateMcpError as exc:
            errors.append(str(exc))
    raise PromptTemplateMcpError("; retry failed after %s attempt(s): %s" % (attempts, " | ".join(errors)))


def mcp_smoke_test(target: Path) -> Mapping[str, Any]:
    payload = mcp_call_tools(target, [("search_prompts", {"query": "type:template poster", "top": 1})])[0]
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise PromptTemplateMcpError("MCP smoke returned no prompt templates")
    first = results[0].get("document") if isinstance(results[0], Mapping) else None
    return {
        "query": payload.get("query"),
        "count": payload.get("count"),
        "topId": first.get("id") if isinstance(first, Mapping) else None,
        "timings": payload.get("timings"),
    }


def full_payload_paths(target: Path) -> list[Path]:
    return [
        target / "data" / "search-docs.json",
        target / "data" / "search-index.json",
        target / "data" / "search-facets.json",
        target / "src" / "prompt_searcher" / "mcp_server.py",
        _venv_python(target),
    ]


def full_payload_present(target: Path) -> bool:
    return all(path.is_file() for path in full_payload_paths(target))


def write_recovered_install_record(
    target: Path,
    *,
    source_root: Path,
    python_executable: Path,
    smoke: Mapping[str, Any],
    recovered_from: str,
) -> None:
    smoke_payload = dict(smoke)
    smoke_payload.setdefault("ok", True)
    smoke_payload["recoveredFrom"] = compact_text(recovered_from, 1000)
    record = {
        "schemaVersion": 1,
        "profile": "full",
        "installedAt": utc_now(),
        "offline": False,
        "sourceRoot": str(source_root.expanduser().resolve()),
        "target": str(target),
        "python": str(python_executable),
        "venv": str(target / ".venv"),
        "dataDir": str(target / "data"),
        "codeFiles": [],
        "dataFiles": [],
        "supportFiles": [],
        "launchers": {
            "posix": str(target / "bin" / "prompt-search"),
            "powershell": str(target / "bin" / "prompt-search.ps1"),
            "mcp": str(target / "bin" / "prompt-search-mcp"),
            "api": str(target / "bin" / "prompt-search-api"),
        },
        "smokeTest": smoke_payload,
    }
    (target / "install.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_ready_marker(target: Path, smoke: Mapping[str, Any], version: PromptMcpVersion | None = None) -> None:
    fingerprint = install_fingerprint(target)
    if fingerprint is None:
        raise PromptTemplateMcpError("cannot write ready marker because required files are missing")
    payload = {
        "schemaVersion": 1,
        "ok": True,
        "source": DEFAULT_REPO,
        "writtenAt": utc_now(),
        "target": str(target),
        "command": mcp_command(target),
        "smoke": dict(smoke),
        "version": version.as_dict() if version else None,
        "fingerprint": fingerprint,
    }
    (target / READY_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def deep_check(
    workspace: str | Path | None = None,
    target: str | Path | None = None,
    *,
    refresh_version: bool = True,
) -> PromptMcpStatus:
    start = perf_counter()
    resolved = resolve_target(workspace, target)
    ok, reason = installed_profile_ok(resolved)
    if not ok:
        return _status(start, ok=False, installed=False, fast=False, target=resolved, reason=reason)
    try:
        smoke = mcp_smoke_test(resolved)
        version = version_status(resolved, refresh_latest=refresh_version)
        write_ready_marker(resolved, smoke, version)
    except (OSError, subprocess.SubprocessError, PromptTemplateMcpError) as exc:
        return _status(start, ok=False, installed=True, fast=False, target=resolved, reason=str(exc))
    return _status(start, ok=True, installed=True, fast=False, target=resolved, reason="MCP smoke passed", version=version)


def install_prompt_mcp(
    workspace: str | Path | None = None,
    target: str | Path | None = None,
    *,
    repo: str | None = None,
    ref: str | None = None,
    timeout: float | None = None,
) -> Mapping[str, Any]:
    resolved = resolve_target(workspace, target)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    repo_url = repo or os.environ.get("ASSETGEN_PROMPT_MCP_REPO") or DEFAULT_REPO
    repo_ref = ref or os.environ.get("ASSETGEN_PROMPT_MCP_REF") or DEFAULT_REF
    timeout = timeout or float(os.environ.get("ASSETGEN_PROMPT_MCP_INSTALL_TIMEOUT", "300"))
    git_bin = git_command()
    with tempfile.TemporaryDirectory(prefix="image-2-prompt-") as tmp:
        source = Path(tmp) / "source"
        clone_cmd = [git_bin, "clone", "--depth", "1", "--branch", repo_ref, repo_url, str(source)]
        clone = subprocess.run(
            clone_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if clone.returncode != 0:
            raise PromptTemplateMcpError(f"git clone failed: {clone.stderr.strip() or clone.stdout.strip()}")
        installed_commit = local_clone_commit(source)
        install_cmd = [
            sys.executable,
            str(source / "install.py"),
            "--target",
            str(resolved),
            "--profile",
            "full",
            "--yes",
        ]
        install = subprocess.run(
            install_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if install.returncode != 0:
            install_error = install.stderr.strip() or install.stdout.strip()[-2000:]
            recovered = False
            if full_payload_present(resolved):
                try:
                    smoke = mcp_smoke_test(resolved)
                    write_recovered_install_record(
                        resolved,
                        source_root=source,
                        python_executable=_venv_python(resolved),
                        smoke=smoke,
                        recovered_from=install_error,
                    )
                    recovered = True
                except (OSError, subprocess.SubprocessError, PromptTemplateMcpError) as exc:
                    install_error = f"{install_error}; recovery smoke failed: {exc}"
            if not recovered:
                raise PromptTemplateMcpError(f"image-2-prompt install failed: {install_error}")
    write_version_file(resolved, repo=repo_url, ref=repo_ref, installed_commit=installed_commit, latest_commit=installed_commit)
    write_latest_cache(resolved, repo=repo_url, ref=repo_ref, latest_commit=installed_commit, source="install")
    return {"target": str(resolved), "repo": repo_url, "ref": repo_ref, "installed_commit": installed_commit}


def ensure_prompt_mcp(
    workspace: str | Path | None = None,
    target: str | Path | None = None,
    *,
    install: bool = True,
    upgrade: bool = False,
    refresh_version: bool = False,
) -> PromptMcpStatus:
    cached = quick_check(workspace, target, refresh_version=refresh_version)
    if cached.ok and not (upgrade and cached.version and (cached.version.upgrade_available or not cached.version.installed_commit)):
        return cached
    checked = deep_check(workspace, target, refresh_version=True)
    if checked.ok or not install:
        if not (upgrade and checked.version and (checked.version.upgrade_available or not checked.version.installed_commit)):
            return checked
    try:
        install_prompt_mcp(workspace, target)
    except (OSError, subprocess.SubprocessError, PromptTemplateMcpError) as exc:
        start = perf_counter()
        resolved = resolve_target(workspace, target)
        return _status(start, ok=False, installed=False, fast=False, target=resolved, reason=str(exc))
    return deep_check(workspace, target, refresh_version=True)


def compact_text(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _join_values(values: Any) -> str:
    if isinstance(values, list):
        return ", ".join(compact_text(item, 80) for item in values if str(item or "").strip())
    return compact_text(values, 200)


def build_template_query(prompt: str, *, asset_role: str, size: str, style: str) -> str:
    parts = ["type:template", "image prompt template", asset_role or "asset"]
    if style and style.strip():
        parts.append(style.strip())
    if size and size.strip():
        parts.append(size.strip())
    parts.append(prompt.strip())
    return " ".join(part for part in parts if part).strip()


def search_prompt_documents(
    workspace: str | Path,
    prompt: str,
    *,
    asset_role: str,
    size: str = "",
    style: str = "",
    top: int = 3,
    target: str | Path | None = None,
) -> tuple[PromptMcpStatus, str, list[Mapping[str, Any]]]:
    status = ensure_prompt_mcp(workspace, target, install=True)
    if not status.ok:
        raise PromptTemplateMcpError(status.reason)
    resolved = Path(status.target)
    query = build_template_query(prompt, asset_role=asset_role, size=size, style=style)
    search = mcp_call_tools(resolved, [("search_prompts", {"query": query, "top": max(1, top)})])[0]
    results = search.get("results")
    if not isinstance(results, list) or not results:
        return status, query, []
    ids: list[str] = []
    fallback_documents: dict[str, Mapping[str, Any]] = {}
    for item in results[:top]:
        document = item.get("document") if isinstance(item, Mapping) else None
        if isinstance(document, Mapping) and isinstance(document.get("id"), str):
            ids.append(document["id"])
            fallback_documents[document["id"]] = document
    if not ids:
        return status, query, []
    extracted: list[Mapping[str, Any]] = []
    for prompt_id in ids:
        payload = mcp_call_tools(resolved, [("get_prompt", {"prompt_id": prompt_id})])[0]
        document = payload.get("document")
        if isinstance(document, Mapping):
            extracted.append(document)
        elif prompt_id in fallback_documents:
            extracted.append(fallback_documents[prompt_id])
    return status, query, extracted


def format_prompt_context(
    *,
    status: PromptMcpStatus,
    query: str,
    documents: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "[IMAGE-2-PROMPT MCP TEMPLATE CONTEXT]",
        "Use these retrieved prompt templates as structure references. Adapt them to the concrete user request; do not copy irrelevant text.",
        f"MCP status: {status.reason}; target={status.target}",
        f"Search query: {query}",
    ]
    if not documents:
        lines.append("No prompt templates matched; proceed from the user request and asset role only.")
        return "\n".join(lines)

    for index, document in enumerate(documents, start=1):
        fields = document.get("fields") if isinstance(document.get("fields"), Mapping) else {}
        facets = document.get("facets") if isinstance(document.get("facets"), Mapping) else {}
        title = compact_text(document.get("title"), 180)
        doc_id = compact_text(document.get("id"), 120)
        doc_type = compact_text(document.get("type"), 80)
        category = compact_text(facets.get("category"), 120)
        styles = _join_values(facets.get("styles"))
        scenes = _join_values(facets.get("scenes"))
        tags = _join_values(facets.get("tags"))
        lines.extend(
            [
                f"Template {index}: {doc_id} | {title} | type={doc_type}",
                f"- Facets: category={category}; styles={styles}; scenes={scenes}; tags={tags}",
            ]
        )
        for label, key, limit in (
            ("Intent", "intent", 420),
            ("Description", "description", 420),
            ("Prompt pattern", "body", 800),
            ("Avoid", "negative", 500),
        ):
            value = compact_text(fields.get(key) or document.get(key), limit)
            if value:
                lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def build_asset_prompt_context(
    workspace: str | Path,
    prompt: str,
    *,
    asset_role: str,
    size: str = "",
    style: str = "",
    top: int = 3,
    target: str | Path | None = None,
) -> dict[str, Any]:
    status, query, documents = search_prompt_documents(
        workspace,
        prompt,
        asset_role=asset_role,
        size=size,
        style=style,
        top=top,
        target=target,
    )
    template_ids = [str(document.get("id")) for document in documents if document.get("id")]
    return {
        "text": format_prompt_context(status=status, query=query, documents=documents),
        "metadata": {
            "status": status.as_dict(),
            "query": query,
            "template_ids": template_ids,
            "template_count": len(documents),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check/install/search the image-2-prompt MCP prompt template engine.")
    parser.add_argument("--workspace", default=".", help="Project workspace. Default: current directory.")
    parser.add_argument("--target", help="Prompt searcher install directory. Default: <workspace>/.prompt-searcher.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Run the fast cached MCP readiness check.")
    check.add_argument("--workspace", default=argparse.SUPPRESS, help="Project workspace. Default: current directory.")
    check.add_argument("--target", default=argparse.SUPPRESS, help="Prompt searcher install directory. Default: <workspace>/.prompt-searcher.")
    check.add_argument("--deep", action="store_true", help="Run MCP smoke instead of only checking the cache.")
    check.add_argument("--refresh-version", action="store_true", help="Refresh latest image-2-prompt commit from the remote.")
    check.add_argument("--json", action="store_true")

    ensure = sub.add_parser("ensure", help="Ensure the full MCP profile is installed and smoke-tested.")
    ensure.add_argument("--workspace", default=argparse.SUPPRESS, help="Project workspace. Default: current directory.")
    ensure.add_argument("--target", default=argparse.SUPPRESS, help="Prompt searcher install directory. Default: <workspace>/.prompt-searcher.")
    ensure.add_argument("--upgrade", action="store_true", help="Reinstall image-2-prompt when local version is unknown or behind latest.")
    ensure.add_argument("--refresh-version", action="store_true", help="Refresh latest image-2-prompt commit from the remote before deciding.")
    ensure.add_argument("--json", action="store_true")

    version = sub.add_parser("version", help="Report installed and latest image-2-prompt versions.")
    version.add_argument("--workspace", default=argparse.SUPPRESS, help="Project workspace. Default: current directory.")
    version.add_argument("--target", default=argparse.SUPPRESS, help="Prompt searcher install directory. Default: <workspace>/.prompt-searcher.")
    version.add_argument("--refresh", action="store_true", help="Refresh latest image-2-prompt commit from the remote.")
    version.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="Search image prompt templates through MCP.")
    search.add_argument("--workspace", default=argparse.SUPPRESS, help="Project workspace. Default: current directory.")
    search.add_argument("--target", default=argparse.SUPPRESS, help="Prompt searcher install directory. Default: <workspace>/.prompt-searcher.")
    search.add_argument("--query", required=True)
    search.add_argument("--asset-role", default="other")
    search.add_argument("--size", default="")
    search.add_argument("--style", default="")
    search.add_argument("--top", type=int, default=3)
    search.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            status = (
                deep_check(args.workspace, args.target, refresh_version=True)
                if args.deep
                else quick_check(args.workspace, args.target, refresh_version=args.refresh_version)
            )
            if args.json:
                print(json.dumps(status.as_dict(), ensure_ascii=False, indent=2))
            else:
                print(("OK" if status.ok else "NOT_READY") + f": {status.reason} ({status.elapsed_ms:.3f} ms)")
            return 0 if status.ok else 1
        if args.command == "ensure":
            status = ensure_prompt_mcp(
                args.workspace,
                args.target,
                install=True,
                upgrade=args.upgrade,
                refresh_version=args.refresh_version,
            )
            if args.json:
                print(json.dumps(status.as_dict(), ensure_ascii=False, indent=2))
            else:
                print(("OK" if status.ok else "ERROR") + f": {status.reason} ({status.elapsed_ms:.3f} ms)")
            return 0 if status.ok else 1
        if args.command == "version":
            target = resolve_target(args.workspace, args.target)
            payload = version_status(target, refresh_latest=args.refresh)
            if args.json:
                print(json.dumps(payload.as_dict(), ensure_ascii=False, indent=2))
            else:
                label = "LATEST" if payload.is_latest else "UPGRADE_CHECK"
                print(f"{label}: installed={payload.installed_commit or '<unknown>'} latest={payload.latest_commit or '<unknown>'}")
                if payload.warning:
                    print(f"WARNING: {payload.warning}")
            return 0 if payload.is_latest else 2
        if args.command == "search":
            context = build_asset_prompt_context(
                args.workspace,
                args.query,
                asset_role=args.asset_role,
                size=args.size,
                style=args.style,
                top=args.top,
                target=args.target,
            )
            if args.json:
                print(json.dumps(context, ensure_ascii=False, indent=2))
            else:
                print(context["text"])
            return 0
    except (OSError, subprocess.SubprocessError, PromptTemplateMcpError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
