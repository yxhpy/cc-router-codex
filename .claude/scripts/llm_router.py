#!/usr/bin/env python3
"""LLM-backed router for controller prompts.

The router classifies a user prompt into a suggested composition of atomic
taskctl capability steps. The controller still executes one capability at a
time, but semantic routing and composition stay in the fast model instead of
regex keyword rules in the hook script.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from project_paths import REPO_ROOT


SCRIPT_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = SCRIPT_DIR.parent
ROLES = {
    "planner",
    "divergent",
    "requirements",
    "prototype",
    "uiux",
    "assetgen",
    "debugger",
    "operator",
    "security",
    "docs",
    "release",
    "fullstack",
    "tester",
    "reviewer",
    "closer",
}
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_ENV_PATH = CLAUDE_DIR / ".env"
ARTIFACT_KIND_BY_EXT = {
    "html": "html",
    "htm": "html",
    "css": "css",
    "js": "js",
    "jsx": "source",
    "ts": "source",
    "tsx": "source",
    "vue": "source",
    "svelte": "source",
    "py": "source",
    "java": "source",
    "go": "source",
    "rs": "source",
    "php": "source",
    "rb": "source",
    "cs": "source",
    "sql": "source",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "webp": "image",
    "gif": "image",
    "svg": "image",
    "avif": "image",
}

ROUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "production_work": {"type": "boolean"},
        "role": {"type": "string", "enum": sorted(ROLES)},
        "title": {"type": "string", "maxLength": 120},
        "worker_prompt": {"type": "string", "maxLength": 4000},
        "artifacts": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[A-Za-z0-9_.-]+:.{1,240}$", "maxLength": 260},
            "maxItems": 8,
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": sorted(ROLES)},
                    "title": {"type": "string", "maxLength": 120},
                    "worker_prompt": {"type": "string", "maxLength": 4000},
                    "artifacts": {
                        "type": "array",
                        "items": {"type": "string", "pattern": "^[A-Za-z0-9_.-]+:.{1,240}$", "maxLength": 260},
                        "maxItems": 8,
                    },
                    "purpose": {"type": "string", "maxLength": 400},
                },
                "required": ["role", "title", "worker_prompt", "artifacts", "purpose"],
                "additionalProperties": False,
            },
            "maxItems": 6,
        },
        "reason": {"type": "string", "maxLength": 600},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "production_work",
        "role",
        "title",
        "worker_prompt",
        "artifacts",
        "steps",
        "reason",
        "confidence",
    ],
    "additionalProperties": False,
}

ROUTER_SYSTEM_PROMPT = """You are the taskctl routing classifier. Return exactly one JSON object matching the schema: production_work, role, title, worker_prompt, artifacts, steps, reason, confidence.

Set production_work=false for conversation, status questions, explanation-only requests, or requests that do not require project artifacts. Set production_work=true for requests to create, modify, verify, review, plan, design, diagnose, operate, secure, document, release, or close project work.

Choose roles by responsibility: planner for plans and architecture notes, divergent for options and tradeoffs, requirements for acceptance criteria, uiux for design artifacts, prototype for specs and interaction contracts, assetgen for local image asset generation or placement, debugger for reproduction/log analysis/root-cause reports, operator for installs/dependencies/CI/Docker/packaging/deploy/runtime health, security for security reviews/threat models/dependency audits/permission analysis, docs for README/runbooks/API notes/changelog prose, release for versioning/tags/release notes/install verification/rollback notes, fullstack for production implementation, tester for reports/screenshots/test files, reviewer for findings, closer for closure summaries.

Artifacts must be strings in kind:path format, never objects. Use neutral examples such as html:sample-page.html, source:src/app.ts, image:assets/generated/sword.png, local_asset_manifest:assets/generated/manifest.json, test_report:.claude/artifacts/test_report.md.

Steps are advisory composition only. The controller will execute one capability at a time and inspect the result before deciding the next step. For production_work=true, include at least one step and mirror the next step into the top-level role/title/worker_prompt/artifacts.

For standalone image asset requests, use assetgen. This includes game assets, web visuals, video thumbnails/key art/overlays, icons, textures, sprites, banners, product renders, and any other picture-related production asset. Assetgen worker prompts should preserve the user's requested subtype: game, web, video, or other, and should target raster files such as png, jpg, jpeg, or webp, never SVG.

For user-facing frontend or high-fidelity visual work, prefer uiux -> assetgen -> fullstack -> tester when new media is needed; otherwise prefer uiux -> fullstack -> tester unless the request is explicitly design-only, test-only, asset-only, or already grounded in project design sources. The uiux step must inspect project DESIGN.md, design tokens, component libraries, screenshots, theme files, or local .claude/design-references. It must record design_reference_selection and style_contract. If needed media is missing, record asset_generation_brief. The assetgen step must generate/place local bitmap image assets from the brief through `.claude/scripts/assetgen_exec.py`, record local_asset_manifest, and avoid remote hotlinks or SVG placeholders. The fullstack step must reference the design source and local asset manifest.

worker_prompt must be a bounded instruction for one Codex worker. Do not include Markdown outside the JSON object.
"""
TASK_INPUT_GUARD_PROMPT = """You are the taskctl worker-input guard. Return exactly one JSON object matching the schema: allowed, has_action, bounded, violation, suggested_role, confidence.

Validate whether the requested worker prompt fits the declared role and is one bounded capability step.

Role boundaries:
- planner: plans, inventories, sequencing notes, architecture notes only.
- divergent: options and tradeoff analysis only.
- requirements: requirements and acceptance checks only.
- uiux: design artifacts only; no production code files.
- prototype: prototype specs, DOM hooks, and interaction contracts only; no production UI code.
- assetgen: local image assets only, including game sprites/icons/textures, web visuals, video thumbnails/key art/overlays, asset_generation_brief, and local_asset_manifest; no product code files.
- debugger: reproduction, logs, root-cause analysis, and minimal fix recommendations only; no product code patches.
- operator: installs, dependencies, builds, CI, Docker, packaging, deploys, runtime health checks, and operational runbooks; no product source edits.
- security: security reviews, threat models, dependency audits, permission analyses, and remediation plans only; no product code patches.
- docs: documentation, runbooks, API notes, README material, and changelog prose only; no product source edits.
- release: versioning, CHANGELOG/release notes, tags, release packaging, install verification, rollback notes, and release audit artifacts; no product source edits.
- fullstack: production implementation code and scripts.
- tester: reports, screenshots, and test files under test paths; no production source edits.
- reviewer: review findings only.
- closer: closure and audit summaries only.

For standalone image asset work, assetgen prompts may generate or place local raster assets through `.claude/scripts/assetgen_exec.py` and record local_asset_manifest. Allowed generated image outputs are png/jpg/jpeg/webp only; do not use SVG for assetgen outputs. They must not create HTML/CSS/JS/TSX/backend/schema/migration files. Assetgen should keep the requested subtype explicit: game asset, web asset, video asset, or other image asset.

For frontend or high-fidelity visual work, uiux prompts must require project design sources or local .claude/design-references and must produce design_reference_selection/style_contract. If new visual media is missing, they should produce asset_generation_brief. Reject uiux prompts that ask the model to invent untraceable visual style.

For fullstack prompts that produce HTML/CSS/JS/TSX/Vue/Svelte or other frontend product files, require an explicit reference to style_contract, design_reference_selection, or an existing project design source. If new visual media is needed, require asset_generation_brief, local bitmap files, local_asset_manifest, and no remote hotlinks. Otherwise allowed=false and suggested_role=uiux.

Reject prompts that are too broad, lack an explicit action, smuggle multiple workflow stages into one capability, or ask an analysis role to implement production code.
"""
TASK_INPUT_GUARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "allowed": {"type": "boolean"},
        "has_action": {"type": "boolean"},
        "bounded": {"type": "boolean"},
        "violation": {"type": "string", "maxLength": 800},
        "suggested_role": {"type": "string", "enum": [*sorted(ROLES), ""]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["allowed", "has_action", "bounded", "violation", "suggested_role", "confidence"],
    "additionalProperties": False,
}


@dataclass
class CapabilityStep:
    role: str = "fullstack"
    title: str = ""
    worker_prompt: str = ""
    artifacts: list[str] = field(default_factory=list)
    purpose: str = ""

    def normalized(self, prompt: str) -> "CapabilityStep":
        if self.role not in ROLES:
            self.role = "fullstack"
        self.title = compact(self.title or prompt, 100)
        self.worker_prompt = self.worker_prompt.strip() or (
            f"Execute one atomic {self.role} capability for this user goal: {prompt}. "
            "Produce or verify the required artifact and stop."
        )
        self.artifacts = normalize_artifacts(self.artifacts)
        self.purpose = compact(self.purpose, 400)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "title": self.title,
            "worker_prompt": self.worker_prompt,
            "artifacts": self.artifacts,
            "purpose": self.purpose,
        }


@dataclass
class Route:
    production_work: bool
    role: str = "fullstack"
    title: str = ""
    worker_prompt: str = ""
    artifacts: list[str] = field(default_factory=list)
    steps: list[CapabilityStep] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0
    source: str = "llm"
    error: str = ""

    def normalized(self, prompt: str) -> "Route":
        self.steps = [step.normalized(prompt) for step in self.steps]
        if self.role not in ROLES:
            self.role = "fullstack"
        self.title = compact(self.title or prompt, 100)
        self.worker_prompt = self.worker_prompt.strip() or (
            f"Execute one atomic {self.role} capability for this user goal: {prompt}. "
            "Produce or verify the required artifact and stop."
        )
        self.artifacts = normalize_artifacts(self.artifacts)
        if self.production_work and not self.steps:
            self.steps = [
                CapabilityStep(
                    role=self.role,
                    title=self.title,
                    worker_prompt=self.worker_prompt,
                    artifacts=self.artifacts,
                    purpose="next atomic capability",
                ).normalized(prompt)
            ]
        if self.steps:
            first = self.steps[0]
            self.role = first.role
            self.title = first.title
            self.worker_prompt = first.worker_prompt
            self.artifacts = first.artifacts
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "production_work": self.production_work,
            "role": self.role,
            "title": self.title,
            "worker_prompt": self.worker_prompt,
            "artifacts": self.artifacts,
            "steps": [step.to_dict() for step in self.steps],
            "reason": self.reason,
            "confidence": self.confidence,
            "source": self.source,
            "error": self.error,
        }


@dataclass
class TaskInputGuard:
    allowed: bool
    has_action: bool = True
    bounded: bool = True
    violation: str = ""
    suggested_role: str = ""
    confidence: float = 0.0
    source: str = "openai"
    error: str = ""

    def normalized(self) -> "TaskInputGuard":
        if self.suggested_role and self.suggested_role not in ROLES:
            self.suggested_role = ""
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        return self


def compact(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def artifact_kind_for_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ARTIFACT_KIND_BY_EXT.get(ext, "artifact")


def normalize_artifacts(values: list[Any]) -> list[str]:
    artifacts: list[str] = []
    for value in values:
        artifact = ""
        if isinstance(value, str):
            artifact = value.strip()
        elif isinstance(value, dict):
            path = str(value.get("path") or value.get("filename") or value.get("file") or "").strip()
            kind = str(value.get("kind") or value.get("type") or "").strip()
            if path:
                if not kind or kind == "file":
                    kind = artifact_kind_for_path(path)
                artifact = f"{kind}:{path}"
        if not artifact:
            continue
        if ":" not in artifact:
            artifact = f"{artifact_kind_for_path(artifact)}:{artifact}"
        if artifact not in artifacts:
            artifacts.append(artifact)
    return artifacts


def route_from_payload(payload: dict[str, Any], prompt: str, source: str = "llm") -> Route:
    steps = [
        step_from_payload(item, prompt)
        for item in (payload.get("steps") or payload.get("recommended_steps") or [])
        if isinstance(item, dict)
    ]
    route = Route(
        production_work=bool(payload.get("production_work")),
        role=str(payload.get("role") or "fullstack"),
        title=str(payload.get("title") or ""),
        worker_prompt=str(payload.get("worker_prompt") or ""),
        artifacts=list(payload.get("artifacts") or []),
        steps=steps,
        reason=str(payload.get("reason") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        source=source,
    ).normalized(prompt)
    if route.artifacts and not route.production_work:
        route.production_work = True
        route.reason = (route.reason + " Consistency correction: artifacts were returned.").strip()
    return route


def step_from_payload(payload: dict[str, Any], prompt: str) -> CapabilityStep:
    return CapabilityStep(
        role=str(payload.get("role") or "fullstack"),
        title=str(payload.get("title") or ""),
        worker_prompt=str(payload.get("worker_prompt") or ""),
        artifacts=list(payload.get("artifacts") or []),
        purpose=str(payload.get("purpose") or ""),
    ).normalized(prompt)


def mock_route(prompt: str) -> Route | None:
    raw = os.environ.get("TASKCTL_ROUTER_MOCK_JSON")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return Route(False, source="mock", error=f"invalid mock JSON: {exc}").normalized(prompt)
    if not isinstance(payload, dict):
        return Route(False, source="mock", error="mock JSON must be an object").normalized(prompt)
    return route_from_payload(payload, prompt, "mock")


def guard_from_payload(payload: dict[str, Any], source: str = "openai") -> TaskInputGuard:
    return TaskInputGuard(
        allowed=bool(payload.get("allowed")),
        has_action=bool(payload.get("has_action", True)),
        bounded=bool(payload.get("bounded", True)),
        violation=str(payload.get("violation") or ""),
        suggested_role=str(payload.get("suggested_role") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        source=source,
    ).normalized()


def mock_task_input_guard() -> TaskInputGuard | None:
    raw = os.environ.get("TASKCTL_INPUT_GUARD_MOCK_JSON")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return TaskInputGuard(False, violation=f"invalid input guard mock JSON: {exc}", source="mock", error=str(exc))
    if not isinstance(payload, dict):
        return TaskInputGuard(False, violation="input guard mock JSON must be an object", source="mock")
    return guard_from_payload(payload, "mock")


def parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def load_project_env(path: Path | None = None) -> dict[str, str]:
    env_path = path or Path(os.environ.get("TASKCTL_ROUTER_ENV_PATH") or DEFAULT_ENV_PATH)
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("router model did not return a JSON object")
    return parsed


def provider_mode(kind: str) -> str:
    specific = os.environ.get(f"TASKCTL_{kind.upper()}_PROVIDER")
    if specific:
        return specific.strip().lower()
    return os.environ.get("TASKCTL_LLM_PROVIDER", "openai").strip().lower()


def codex_fallback_enabled(kind: str) -> bool:
    value = (
        os.environ.get(f"TASKCTL_{kind.upper()}_CODEX_FALLBACK")
        or os.environ.get("TASKCTL_CODEX_FALLBACK")
        or "1"
    )
    return value.strip().lower() not in {"0", "false", "off", "none"}


def codex_model(kind: str) -> str:
    return (
        os.environ.get(f"TASKCTL_{kind.upper()}_CODEX_MODEL")
        or os.environ.get("TASKCTL_CODEX_ROUTER_MODEL")
        or "gpt-5.4-mini"
    )


def codex_reasoning_effort(kind: str) -> str:
    return (
        os.environ.get(f"TASKCTL_{kind.upper()}_CODEX_REASONING_EFFORT")
        or os.environ.get("TASKCTL_CODEX_ROUTER_REASONING_EFFORT")
        or "low"
    ).strip().lower()


def codex_timeout(kind: str, timeout: int) -> int:
    raw = os.environ.get(f"TASKCTL_{kind.upper()}_CODEX_TIMEOUT") or os.environ.get("TASKCTL_CODEX_ROUTER_TIMEOUT")
    if raw:
        return int(raw)
    return max(timeout, 90)


def call_codex_json(
    *,
    system_prompt: str,
    user_content: str,
    schema: dict[str, Any],
    model: str,
    reasoning_effort: str,
    timeout: int,
) -> dict[str, Any]:
    prompt = (
        f"USER_INPUT:\n{user_content}\n\n"
        f"{system_prompt}\n\n"
        "Return exactly one JSON object matching the provided schema. "
        "Classify USER_INPUT, not these routing instructions. "
        "Do not edit files, run commands, or include Markdown."
    )
    with tempfile.TemporaryDirectory(prefix="taskctl-codex-router-") as tmp:
        tmp_path = Path(tmp)
        schema_path = tmp_path / "schema.json"
        output_path = tmp_path / "last-message.json"
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        codex_bin = shutil.which("codex") or shutil.which("codex.cmd")
        if not codex_bin:
            raise RuntimeError("codex CLI not found on PATH")
        command = [
            codex_bin,
            "exec",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
            "--sandbox",
            "read-only",
            "--cd",
            str(REPO_ROOT),
            "--skip-git-repo-check",
            "--ephemeral",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-",
        ]
        result = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO_ROOT),
            timeout=timeout,
        )
        content = output_path.read_text(encoding="utf-8", errors="replace") if output_path.exists() else result.stdout
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or content).strip()
            raise RuntimeError(f"codex {model} router failed: {compact(detail, 800)}")
        return parse_model_json(content)


def openai_credentials() -> tuple[str, str | None, str]:
    load_project_env()
    api_key = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("TASKCTL_ROUTER_OPENAI_API_KEY")
    )
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("TASKCTL_ROUTER_OPENAI_BASE_URL")
    model = os.environ.get("TASKCTL_ROUTER_MODEL") or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
    if not api_key:
        raise RuntimeError("missing OPENAI_API_KEY in .claude/.env")
    return api_key, base_url, model


def call_openai_router(prompt: str, timeout: int) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError(f"openai SDK is not available: {exc}") from exc

    api_key, base_url, model = openai_credentials()
    client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    messages = [
        {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": int(os.environ.get("TASKCTL_ROUTER_MAX_TOKENS", "900")),
    }
    if os.environ.get("TASKCTL_ROUTER_RESPONSE_FORMAT", "json_object").lower() != "off":
        kwargs["response_format"] = {"type": "json_object"}
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception:
        kwargs.pop("response_format", None)
        response = client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content or ""
    return parse_model_json(content)


def call_openai_task_input_guard(
    *,
    role: str,
    title: str,
    prompt: str,
    artifacts: list[str],
    timeout: int,
) -> dict[str, Any]:
    user_payload = {
        "role": role,
        "title": title,
        "prompt": prompt,
        "artifacts": artifacts,
    }
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError(f"openai SDK is not available: {exc}") from exc

    api_key, base_url, model = openai_credentials()
    client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    kwargs: dict[str, Any] = {
        "model": os.environ.get("TASKCTL_INPUT_GUARD_MODEL") or model,
        "messages": [
            {"role": "system", "content": TASK_INPUT_GUARD_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": int(os.environ.get("TASKCTL_INPUT_GUARD_MAX_TOKENS", "500")),
    }
    if os.environ.get("TASKCTL_INPUT_GUARD_RESPONSE_FORMAT", "json_object").lower() != "off":
        kwargs["response_format"] = {"type": "json_object"}
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception:
        kwargs.pop("response_format", None)
        response = client.chat.completions.create(**kwargs)
    return parse_model_json(response.choices[0].message.content or "")


def guard_task_input(
    *,
    role: str,
    title: str,
    prompt: str,
    artifacts: list[str],
    timeout: int | None = None,
) -> TaskInputGuard:
    load_project_env()
    mocked = mock_task_input_guard()
    if mocked is not None:
        return mocked
    if os.environ.get("TASKCTL_INPUT_GUARD", "llm").strip().lower() in {"0", "false", "off", "none"}:
        return TaskInputGuard(True, source="disabled")
    timeout = timeout or int(os.environ.get("TASKCTL_INPUT_GUARD_TIMEOUT", os.environ.get("TASKCTL_ROUTER_TIMEOUT", "25")))
    mode = provider_mode("input_guard")
    try:
        if mode == "codex":
            payload = call_codex_json(
                system_prompt=TASK_INPUT_GUARD_PROMPT,
                user_content=json.dumps(
                    {"role": role, "title": title, "prompt": prompt, "artifacts": artifacts},
                    ensure_ascii=False,
                ),
                schema=TASK_INPUT_GUARD_SCHEMA,
                model=codex_model("input_guard"),
                reasoning_effort=codex_reasoning_effort("input_guard"),
                timeout=codex_timeout("input_guard", timeout),
            )
            return guard_from_payload(payload, "codex")
        payload = call_openai_task_input_guard(
            role=role,
            title=title,
            prompt=prompt,
            artifacts=artifacts,
            timeout=timeout,
        )
        return guard_from_payload(payload, "openai")
    except Exception as exc:
        if mode != "codex" and codex_fallback_enabled("input_guard"):
            try:
                payload = call_codex_json(
                    system_prompt=TASK_INPUT_GUARD_PROMPT,
                    user_content=json.dumps(
                        {"role": role, "title": title, "prompt": prompt, "artifacts": artifacts},
                        ensure_ascii=False,
                    ),
                        schema=TASK_INPUT_GUARD_SCHEMA,
                        model=codex_model("input_guard"),
                        reasoning_effort=codex_reasoning_effort("input_guard"),
                        timeout=codex_timeout("input_guard", timeout),
                    )
                guard = guard_from_payload(payload, "codex")
                guard.error = f"openai guard fallback used after error: {exc}"
                return guard
            except Exception as codex_exc:
                exc = RuntimeError(f"{exc}; codex fallback failed: {codex_exc}")
        return TaskInputGuard(
            allowed=False,
            has_action=True,
            bounded=False,
            violation="LLM task input guard failed; refusing to bypass role boundaries.",
            confidence=0.0,
            source="fallback",
            error=str(exc),
        )


def route_prompt(prompt: str, timeout: int | None = None) -> Route:
    load_project_env()
    prompt = str(prompt or "").strip()
    if not prompt:
        return Route(False, source="empty").normalized(prompt)
    mocked = mock_route(prompt)
    if mocked is not None:
        return mocked
    timeout = timeout or int(os.environ.get("TASKCTL_ROUTER_TIMEOUT", "25"))
    if os.environ.get("TASKCTL_ROUTER", "llm").strip().lower() in {"0", "false", "off", "none"}:
        return Route(False, source="disabled").normalized(prompt)
    mode = provider_mode("router")
    try:
        if mode == "codex":
            payload = call_codex_json(
                system_prompt=ROUTER_SYSTEM_PROMPT,
                user_content=prompt,
                schema=ROUTER_SCHEMA,
                model=codex_model("router"),
                reasoning_effort=codex_reasoning_effort("router"),
                timeout=codex_timeout("router", timeout),
            )
            return route_from_payload(payload, prompt, "codex")
        payload = call_openai_router(prompt, timeout)
        return route_from_payload(payload, prompt, "openai")
    except Exception as exc:
        if mode != "codex" and codex_fallback_enabled("router"):
            try:
                payload = call_codex_json(
                    system_prompt=ROUTER_SYSTEM_PROMPT,
                    user_content=prompt,
                    schema=ROUTER_SCHEMA,
                    model=codex_model("router"),
                    reasoning_effort=codex_reasoning_effort("router"),
                    timeout=codex_timeout("router", timeout),
                )
                route = route_from_payload(payload, prompt, "codex")
                route.error = f"openai router fallback used after error: {exc}"
                return route
            except Exception as codex_exc:
                exc = RuntimeError(f"{exc}; codex fallback failed: {codex_exc}")
        return Route(
            production_work=True,
            role="fullstack",
            title=compact(prompt, 100),
            worker_prompt=(
                f"Route fallback: execute one bounded capability for this user goal: {prompt}. "
                "If this is not production work, report that no code task is needed. Otherwise produce or verify the requested artifact and stop."
            ),
            artifacts=[],
            reason="LLM router failed; using conservative control-plane fallback.",
            confidence=0.0,
            source="fallback",
            error=str(exc),
        ).normalized(prompt)


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Route a user prompt through the fast LLM router")
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    prompt = args.prompt
    if prompt is None and not sys.stdin.isatty():
        prompt = sys.stdin.read()
    route = route_prompt(prompt or "", args.timeout)
    payload = route.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0 if not route.error else 1


if __name__ == "__main__":
    raise SystemExit(main())
