#!/usr/bin/env python3
"""Unit tests for skill source-of-truth governance."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = ROOT / "tools" / "skill_manifest_check.py"
if not CHECKER_PATH.is_file():
    CHECKER_PATH = ROOT / ".claude" / "scripts" / "skill_manifest_check.py"
SPEC = importlib.util.spec_from_file_location("skill_manifest_check", CHECKER_PATH)
checker = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def write_skill(path: Path, name: str, description: str = "Test skill.") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "---",
                "",
                f"# {name}",
                "",
                "Test body.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_manifest(root: Path, skills: list[dict[str, object]]) -> None:
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "skill-manifest.json").write_text(
        json.dumps({"schema_version": 1, "skills": skills}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class SkillManifestCheckTests(unittest.TestCase):
    def test_current_repository_manifest_passes(self) -> None:
        result = checker.check_repository(ROOT)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.errors, [])

    def test_missing_published_skill_manifest_entry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_skill(root / ".claude" / "skills" / "decompose", "decompose")
            write_manifest(root, [])

            result = checker.check_repository(root)

        self.assertFalse(result.ok)
        self.assertTrue(any("missing manifest entry: decompose" in error for error in result.errors))

    def test_draft_skill_must_not_be_published(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_skill(root / ".claude" / "skills" / "draft-idea", "draft-idea")
            write_manifest(
                root,
                [
                    {
                        "name": "draft-idea",
                        "bucket": "draft",
                        "source": ".claude/skills/draft-idea",
                        "bridges": [{"surface": "claude", "path": ".claude/skills/draft-idea"}],
                    }
                ],
            )

            result = checker.check_repository(root)

        self.assertFalse(result.ok)
        self.assertTrue(any("non-distributable skill is published: draft-idea" in error for error in result.errors))

    def test_plugin_bridge_name_must_match_manifest_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_skill(root / ".claude" / "skills" / "decompose", "decompose")
            write_skill(root / ".claude" / "plugins" / "task-decompose" / "skills" / "decompose", "wrong")
            write_manifest(
                root,
                [
                    {
                        "name": "decompose",
                        "bucket": "distributable",
                        "source": ".claude/skills/decompose",
                        "bridges": [
                            {"surface": "claude", "path": ".claude/skills/decompose"},
                            {
                                "surface": "codex-plugin",
                                "plugin": "task-decompose",
                                "path": ".claude/plugins/task-decompose/skills/decompose",
                            },
                        ],
                    }
                ],
            )

            result = checker.check_repository(root)

        self.assertFalse(result.ok)
        self.assertTrue(any("bridge name mismatch: decompose" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
