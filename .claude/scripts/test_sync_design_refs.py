#!/usr/bin/env python3
"""Unit tests for sync_design_refs.py."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import sync_design_refs


class SyncDesignRefsTests(unittest.TestCase):
    def test_manifest_indexes_design_files_and_usage_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo-a" / "examples").mkdir(parents=True)
            (root / "repo-a" / "examples" / "DESIGN.md").write_text("# Design", encoding="utf-8")

            manifest = sync_design_refs.write_manifest(root, [])
            payload = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(payload["design_files"], ["repo-a/examples/DESIGN.md"])
        self.assertIn("project_design_first", payload["usage"])
        self.assertIn("not_hardcoded", payload["usage"])

    def test_missing_git_can_still_write_manifest_unless_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sync_design_refs.shutil, "which", return_value=None):
                code = sync_design_refs.main(["--root", tmp, "--quiet"])

            manifest = Path(tmp) / "manifest.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["repos"][0]["action"], "skipped")
        self.assertEqual(payload["repos"][0]["status"], "git unavailable")
        self.assertEqual(payload["repos"][0]["path"], "google-labs-code-design.md")

    def test_missing_git_required_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sync_design_refs.shutil, "which", return_value=None):
                with self.assertRaises(SystemExit):
                    sync_design_refs.main(["--root", tmp, "--quiet", "--require-git"])

    def test_offline_mode_uses_local_cached_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cached = root / "google-labs-code-design.md" / "examples"
            cached.mkdir(parents=True)
            (cached / "DESIGN.md").write_text("# Design", encoding="utf-8")

            code = sync_design_refs.main(["--root", tmp, "--quiet", "--offline"])
            payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(code, 1)
        self.assertEqual(payload["repos"][0]["action"], "local")
        self.assertEqual(payload["repos"][0]["path"], "google-labs-code-design.md")
        self.assertEqual(payload["repos"][1]["action"], "missing")
        self.assertEqual(payload["design_files"], ["google-labs-code-design.md/examples/DESIGN.md"])

    def test_offline_mode_rejects_empty_cached_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "google-labs-code-design.md").mkdir()

            code = sync_design_refs.main(["--root", tmp, "--quiet", "--offline"])
            payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(code, 1)
        self.assertEqual(payload["repos"][0]["action"], "invalid")
        self.assertIn("no DESIGN.md", payload["repos"][0]["status"])


if __name__ == "__main__":
    unittest.main()
