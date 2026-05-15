#!/usr/bin/env python3
"""Unit tests for the LLM-backed router."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import llm_router


class LlmRouterTests(unittest.TestCase):
    def test_normalizes_artifact_objects_from_provider(self) -> None:
        route = llm_router.route_from_payload(
            {
                "production_work": True,
                "role": "fullstack",
                "title": "Create page",
                "worker_prompt": "Create the requested page and stop.",
                "artifacts": [{"type": "file", "filename": "sample-page.html"}],
                "reason": "page target",
                "confidence": 0.9,
            },
            "sample-page.html",
        )

        self.assertEqual(route.artifacts, ["html:sample-page.html"])

    def test_normalizes_image_targets_for_assetgen_role(self) -> None:
        route = llm_router.route_from_payload(
            {
                "production_work": True,
                "role": "assetgen",
                "title": "Generate game sprite",
                "worker_prompt": "Generate a game sprite image and record the image artifact.",
                "artifacts": [{"type": "file", "filename": "assets/generated/crystal-sword.png"}],
                "reason": "standalone image asset request",
                "confidence": 0.9,
            },
            "Generate a game asset sprite.",
        )

        self.assertEqual(route.role, "assetgen")
        self.assertEqual(route.artifacts, ["image:assets/generated/crystal-sword.png"])

    def test_uses_first_suggested_step_as_next_capability(self) -> None:
        route = llm_router.route_from_payload(
            {
                "production_work": True,
                "role": "fullstack",
                "title": "Create sample page",
                "worker_prompt": "Create sample-page.html directly.",
                "artifacts": ["html:sample-page.html"],
                "steps": [
                    {
                        "role": "uiux",
                        "title": "Select sample page design reference",
                        "worker_prompt": "Use project design sources or .claude/design-references and record the style contract.",
                        "artifacts": [
                            "design_reference_selection:.claude/artifacts/design_reference_selection.md",
                            "style_contract:.claude/artifacts/style_contract.md",
                        ],
                        "purpose": "establish traceable visual source",
                    },
                    {
                        "role": "fullstack",
                        "title": "Implement sample page",
                        "worker_prompt": "Implement sample-page.html from the style contract.",
                        "artifacts": ["html:sample-page.html"],
                    },
                ],
                "reason": "frontend visual work needs design source first",
                "confidence": 0.9,
            },
            "Create a sample listing page and save it as sample-page.html.",
        )

        self.assertEqual(route.role, "uiux")
        self.assertEqual(
            route.artifacts,
            [
                "design_reference_selection:.claude/artifacts/design_reference_selection.md",
                "style_contract:.claude/artifacts/style_contract.md",
            ],
        )
        self.assertEqual([step.role for step in route.steps], ["uiux", "fullstack"])

    def test_loads_project_env_without_overwriting_process_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=from-file\n"
                "OPENAI_BASE_URL=https://example.test/v1\n"
                "TASKCTL_ROUTER_MODEL=deepseek-v4-flash\n",
                encoding="utf-8",
            )
            old_key = os.environ.get("OPENAI_API_KEY")
            os.environ["OPENAI_API_KEY"] = "from-process"
            try:
                loaded = llm_router.load_project_env(env_path)
                self.assertEqual(loaded["OPENAI_API_KEY"], "from-file")
                self.assertEqual(os.environ["OPENAI_API_KEY"], "from-process")
            finally:
                if old_key is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = old_key

    def test_mock_route_bypasses_openai(self) -> None:
        payload = {
            "production_work": True,
            "role": "tester",
            "title": "Verify",
            "worker_prompt": "Verify and stop.",
            "artifacts": ["test_report:.claude/artifacts/test_report.md"],
            "reason": "mock",
            "confidence": 0.8,
        }
        with mock.patch.dict(os.environ, {"TASKCTL_ROUTER_MOCK_JSON": json.dumps(payload)}, clear=False):
            route = llm_router.route_prompt("Verify the current project flow.")

        self.assertEqual(route.source, "mock")
        self.assertEqual(route.role, "tester")
        self.assertEqual(route.artifacts, ["test_report:.claude/artifacts/test_report.md"])

    def test_codex_router_provider_uses_configured_mini_model(self) -> None:
        payload = {
            "production_work": True,
            "role": "uiux",
            "title": "Select style",
            "worker_prompt": "Select design references and stop.",
            "artifacts": ["style_contract:.claude/artifacts/style_contract.md"],
            "steps": [],
            "reason": "frontend visual source",
            "confidence": 0.9,
        }
        with (
            mock.patch.dict(
                os.environ,
                {"TASKCTL_ROUTER_PROVIDER": "codex", "TASKCTL_ROUTER_CODEX_MODEL": "gpt-5.4-mini"},
                clear=False,
            ),
            mock.patch.object(llm_router, "call_codex_json", return_value=payload) as codex_call,
        ):
            route = llm_router.route_prompt("Create a high-fidelity frontend page.")

        self.assertEqual(route.source, "codex")
        self.assertEqual(route.role, "uiux")
        self.assertEqual(codex_call.call_args.kwargs["model"], "gpt-5.4-mini")

    def test_openai_router_failure_falls_back_to_codex_mini(self) -> None:
        payload = {
            "production_work": True,
            "role": "uiux",
            "title": "Select style",
            "worker_prompt": "Select design references and stop.",
            "artifacts": ["style_contract:.claude/artifacts/style_contract.md"],
            "steps": [],
            "reason": "fallback",
            "confidence": 0.8,
        }
        with (
            mock.patch.dict(
                os.environ,
                {"TASKCTL_ROUTER_PROVIDER": "openai", "TASKCTL_ROUTER_CODEX_MODEL": "gpt-5.4-mini"},
                clear=False,
            ),
            mock.patch.object(llm_router, "call_openai_router", side_effect=ValueError("bad json")),
            mock.patch.object(llm_router, "call_codex_json", return_value=payload) as codex_call,
        ):
            route = llm_router.route_prompt("Create a high-fidelity frontend page.")

        self.assertEqual(route.source, "codex")
        self.assertIn("openai router fallback used", route.error)
        self.assertEqual(codex_call.call_args.kwargs["model"], "gpt-5.4-mini")

    def test_mock_task_input_guard_bypasses_openai(self) -> None:
        payload = {
            "allowed": False,
            "has_action": True,
            "bounded": True,
            "violation": "role mismatch",
            "suggested_role": "fullstack",
            "confidence": 0.8,
        }
        with mock.patch.dict(os.environ, {"TASKCTL_INPUT_GUARD_MOCK_JSON": json.dumps(payload)}, clear=False):
            guard = llm_router.guard_task_input(
                role="uiux",
                title="Create page",
                prompt="Create sample-page.html with JavaScript interactions.",
                artifacts=["html:sample-page.html"],
            )

        self.assertEqual(guard.source, "mock")
        self.assertFalse(guard.allowed)
        self.assertEqual(guard.suggested_role, "fullstack")
        self.assertEqual(guard.violation, "role mismatch")


if __name__ == "__main__":
    unittest.main()
