#!/usr/bin/env python3
"""Unit tests for model_policy.py."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import model_policy


class ModelPolicyTests(unittest.TestCase):
    def test_ignores_workflow_templates_and_uses_role_policy(self) -> None:
        policy = {
            "default": {"model": "gpt-5.4", "reasoning_effort": "low"},
            "roles": {"tester": {"model": "gpt-5.4", "reasoning_effort": "medium"}},
            "workflows": {
                "existing-system": {
                    "tester": {"model": "gpt-5.4", "reasoning_effort": "high", "why": "regression"}
                }
            },
        }

        choice = model_policy.select_model("existing-system", "tester", policy)

        self.assertEqual(choice.model, "gpt-5.4")
        self.assertEqual(choice.reasoning_effort, "medium")
        self.assertEqual(choice.source, "role:tester")

    def test_role_default_falls_back_to_flagship_for_fullstack(self) -> None:
        choice = model_policy.select_model("general", "fullstack", model_policy.FALLBACK_POLICY)

        self.assertEqual(choice.model, "gpt-5.5")
        self.assertEqual(choice.reasoning_effort, "high")
        self.assertEqual(choice.source, "role:fullstack")

    def test_assetgen_uses_mini_model_for_prompt_template_adapter(self) -> None:
        choice = model_policy.select_model("general", "assetgen", model_policy.FALLBACK_POLICY)

        self.assertEqual(choice.model, "gpt-5.4-mini")
        self.assertEqual(choice.reasoning_effort, "medium")
        self.assertEqual(choice.source, "role:assetgen")

    def test_specialized_roles_have_model_policy_entries(self) -> None:
        expected = {
            "debugger": ("gpt-5.5", "high"),
            "operator": ("gpt-5.4", "medium"),
            "security": ("gpt-5.5", "high"),
            "docs": ("gpt-5.4", "medium"),
            "release": ("gpt-5.4", "medium"),
        }
        for role, (model, effort) in expected.items():
            with self.subTest(role=role):
                choice = model_policy.select_model("general", role, model_policy.FALLBACK_POLICY)
                self.assertEqual(choice.model, model)
                self.assertEqual(choice.reasoning_effort, effort)
                self.assertEqual(choice.source, f"role:{role}")

    def test_env_overrides_are_visible_without_losing_policy_source(self) -> None:
        choice = model_policy.ModelChoice("gpt-5.5", "high", "role:reviewer", "review")
        resolved = model_policy.apply_env_overrides(
            choice,
            {"CODEX_MODEL": "gpt-5.4", "CODEX_REASONING_EFFORT": "medium"},
        )

        self.assertEqual(resolved["model"], "gpt-5.4")
        self.assertEqual(resolved["reasoning_effort"], "medium")
        self.assertTrue(resolved["model_overridden_by_env"])
        self.assertEqual(resolved["source"], "role:reviewer")

    def test_loads_custom_policy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(
                json.dumps({"default": {"model": "gpt-5.4-mini", "reasoning_effort": "low"}}),
                encoding="utf-8",
            )

            policy = model_policy.load_policy(str(path), {})
            choice = model_policy.select_model("any", "unknown", policy)

        self.assertEqual(choice.model, "gpt-5.4-mini")
        self.assertEqual(choice.reasoning_effort, "low")


if __name__ == "__main__":
    unittest.main()
