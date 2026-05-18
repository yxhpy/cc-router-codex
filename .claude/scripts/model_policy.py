#!/usr/bin/env python3
"""Model routing policy for taskctl-managed Codex workers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = SCRIPT_DIR.parent
DEFAULT_POLICY_PATH = CLAUDE_DIR / "model_policy.json"

VALID_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}

FALLBACK_POLICY: dict[str, Any] = {
    "version": 1,
    "default": {
        "model": "gpt-5.5",
        "reasoning_effort": "medium",
        "why": "Fallback policy: use the current flagship for complex reasoning and coding.",
    },
    "roles": {
        "planner": {"model": "gpt-5.5", "reasoning_effort": "xhigh"},
        "divergent": {"model": "gpt-5.5", "reasoning_effort": "medium"},
        "requirements": {"model": "gpt-5.5", "reasoning_effort": "medium"},
        "uiux": {"model": "gpt-5.5", "reasoning_effort": "xhigh"},
        "prototype": {"model": "gpt-5.5", "reasoning_effort": "xhigh"},
        "assetgen": {"model": "gpt-5.4-mini", "reasoning_effort": "medium"},
        "debugger": {"model": "gpt-5.5", "reasoning_effort": "xhigh"},
        "operator": {"model": "gpt-5.4", "reasoning_effort": "medium"},
        "security": {"model": "gpt-5.5", "reasoning_effort": "xhigh"},
        "docs": {"model": "gpt-5.4", "reasoning_effort": "medium"},
        "release": {"model": "gpt-5.4", "reasoning_effort": "medium"},
        "fullstack": {"model": "gpt-5.5", "reasoning_effort": "xhigh"},
        "tester": {"model": "gpt-5.4", "reasoning_effort": "medium"},
        "reviewer": {"model": "gpt-5.5", "reasoning_effort": "xhigh"},
        "closer": {"model": "gpt-5.4", "reasoning_effort": "medium"},
    },
}

FAST_ROLE_OVERRIDES: dict[str, dict[str, str]] = {
    "planner": {"model": "gpt-5.4", "reasoning_effort": "medium"},
    "divergent": {"model": "gpt-5.4-mini", "reasoning_effort": "low"},
    "requirements": {"model": "gpt-5.4", "reasoning_effort": "medium"},
    "uiux": {"model": "gpt-5.4", "reasoning_effort": "medium"},
    "prototype": {"model": "gpt-5.4", "reasoning_effort": "medium"},
    "assetgen": {"model": "gpt-5.4-mini", "reasoning_effort": "low"},
    "debugger": {"model": "gpt-5.4", "reasoning_effort": "medium"},
    "operator": {"model": "gpt-5.4-mini", "reasoning_effort": "low"},
    "security": {"model": "gpt-5.4", "reasoning_effort": "medium"},
    "docs": {"model": "gpt-5.4-mini", "reasoning_effort": "low"},
    "release": {"model": "gpt-5.4-mini", "reasoning_effort": "low"},
    "fullstack": {"model": "gpt-5.4", "reasoning_effort": "medium"},
    "tester": {"model": "gpt-5.4-mini", "reasoning_effort": "low"},
    "reviewer": {"model": "gpt-5.4", "reasoning_effort": "medium"},
    "closer": {"model": "gpt-5.4-mini", "reasoning_effort": "low"},
}


@dataclass(frozen=True)
class ModelChoice:
    model: str
    reasoning_effort: str
    source: str
    why: str = ""


def speed_profile(value: str | None = None, env: Mapping[str, str] | None = None) -> str:
    env = env or os.environ
    raw = str(value or env.get("TASKCTL_SPEED_PROFILE") or env.get("TASKCTL_DEFAULT_SPEED_PROFILE") or "").strip().lower()
    if raw in {"fast", "quick", "interactive"}:
        return "fast"
    return "quality"


def policy_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    return env.get("TASKCTL_MODEL_POLICY", "auto").strip().lower() not in {"0", "false", "off", "none"}


def policy_path(value: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    env = env or os.environ
    raw = value or env.get("TASKCTL_MODEL_POLICY_PATH") or str(DEFAULT_POLICY_PATH)
    return Path(raw).expanduser().resolve()


def load_policy(path_value: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    path = policy_path(path_value, env)
    if not path.exists():
        return FALLBACK_POLICY
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"model policy must be a JSON object: {path}")
    return loaded


def _entry(policy: Mapping[str, Any], _workflow: str, role: str) -> tuple[Mapping[str, Any], str]:
    roles = policy.get("roles", {})
    if isinstance(roles, Mapping):
        role_entry = roles.get(role)
        if isinstance(role_entry, Mapping):
            return role_entry, f"role:{role}"

    default = policy.get("default", {})
    if isinstance(default, Mapping):
        return default, "default"
    return FALLBACK_POLICY["default"], "fallback"


def _clean_model(value: Any) -> str:
    model = str(value or "").strip()
    if not model:
        raise ValueError("model policy entry requires a model")
    return model


def _clean_effort(value: Any) -> str:
    effort = str(value or "medium").strip().lower()
    if effort not in VALID_EFFORTS:
        raise ValueError(f"unsupported reasoning effort in model policy: {effort}")
    return effort


def select_model(
    workflow: str,
    role: str,
    policy: Mapping[str, Any] | None = None,
    speed: str | None = None,
) -> ModelChoice:
    loaded = policy or load_policy()
    entry, source = _entry(loaded, workflow or "general", role)
    profile = speed_profile(speed)
    if profile == "fast" and role in FAST_ROLE_OVERRIDES:
        override = FAST_ROLE_OVERRIDES[role]
        return ModelChoice(
            model=_clean_model(override.get("model")),
            reasoning_effort=_clean_effort(override.get("reasoning_effort")),
            source=f"fast:{role}",
            why="Fast profile: lower-latency interactive worker selection.",
        )
    return ModelChoice(
        model=_clean_model(entry.get("model")),
        reasoning_effort=_clean_effort(entry.get("reasoning_effort")),
        source=source,
        why=str(entry.get("why", "")).strip(),
    )


def apply_env_overrides(choice: ModelChoice, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = env or os.environ
    return {
        "model": env.get("CODEX_MODEL") or choice.model,
        "reasoning_effort": env.get("CODEX_REASONING_EFFORT") or choice.reasoning_effort,
        "source": choice.source,
        "why": choice.why,
        "model_overridden_by_env": bool(env.get("CODEX_MODEL")),
        "reasoning_overridden_by_env": bool(env.get("CODEX_REASONING_EFFORT")),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Resolve the model for a taskctl worker role")
    parser.add_argument("workflow")
    parser.add_argument("role")
    parser.add_argument("--policy")
    parser.add_argument("--speed-profile", choices=["quality", "fast"], default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    choice = select_model(args.workflow, args.role, load_policy(args.policy), speed=args.speed_profile)
    payload = apply_env_overrides(choice)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{payload['model']} {payload['reasoning_effort']} ({payload['source']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
