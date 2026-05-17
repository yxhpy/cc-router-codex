#!/usr/bin/env python3
"""Tests for lightweight project runtime initialization."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import project_init
import route_cache
import taskctl


class ProjectInitTests(unittest.TestCase):
    def test_auto_init_creates_only_mutable_project_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "demo-project"

            runtime = project_init.ensure_project_initialized(workspace, source="test")

            self.assertTrue(runtime.initialized)
            self.assertFalse(runtime.full_install)
            self.assertTrue((workspace / ".claude" / "cc-router-project.json").is_file())
            self.assertTrue((workspace / ".claude" / ".env").is_file())
            self.assertTrue((workspace / ".claude" / ".gitignore").is_file())
            self.assertTrue((workspace / ".claude" / "artifacts").is_dir())
            self.assertTrue((workspace / ".claude" / "task-plans").is_dir())
            self.assertFalse((workspace / ".claude" / "scripts" / "taskctl.py").exists())
            self.assertFalse((workspace / "CLAUDE.md").exists())

            env_text = (workspace / ".claude" / ".env").read_text(encoding="utf-8")
            self.assertIn("TASKCTL_PROJECT_RUNTIME=1", env_text)
            self.assertIn("TASKCTL_GLOBAL_CONTROL_PLANE=", env_text)

    def test_auto_init_preserves_full_project_installs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "installed-project"
            scripts_dir = workspace / ".claude" / "scripts"
            scripts_dir.mkdir(parents=True)
            (scripts_dir / "taskctl.py").write_text("# installed\n", encoding="utf-8")

            runtime = project_init.ensure_project_initialized(workspace, source="test")

            self.assertTrue(runtime.initialized)
            self.assertTrue(runtime.full_install)
            self.assertFalse((workspace / ".claude" / "cc-router-project.json").exists())
            self.assertFalse((workspace / ".claude" / ".env").exists())

    def test_project_environment_tracks_current_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"

            with mock.patch.dict(os.environ, {}, clear=True):
                first_runtime = project_init.apply_project_environment(first)
                second_runtime = project_init.apply_project_environment(second)

                self.assertEqual(os.environ["TASKCTL_WORKSPACE"], str(second_runtime.workspace))
                self.assertEqual(os.environ["TASKCTL_DB"], str(second_runtime.db_path))
                self.assertEqual(os.environ["TASKCTL_ROUTE_CACHE_PATH"], str(second_runtime.route_cache_path))
                self.assertEqual(os.environ["TASKCTL_ROUTER_ENV_PATH"], str(second_runtime.env_path))
                self.assertNotEqual(first_runtime.db_path, second_runtime.db_path)

    def test_project_environment_preserves_explicit_route_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            explicit_cache = Path(tmp) / "route-cache.json"

            with mock.patch.dict(os.environ, {"TASKCTL_ROUTE_CACHE_PATH": str(explicit_cache)}, clear=True):
                runtime = project_init.apply_project_environment(workspace)

                self.assertEqual(os.environ["TASKCTL_DB"], str(runtime.db_path))
                self.assertEqual(os.environ["TASKCTL_ROUTE_CACHE_PATH"], str(explicit_cache))
                self.assertNotIn("TASKCTL_ROUTE_CACHE_PATH_AUTOINIT", os.environ)

    def test_taskctl_and_route_cache_use_project_cwd_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "cwd-project"
            project_init.ensure_project_initialized(workspace, source="test")
            previous = Path.cwd()

            with mock.patch.dict(os.environ, {}, clear=True):
                try:
                    os.chdir(workspace)
                    self.assertEqual(taskctl.db_path(), workspace.resolve() / ".claude" / "taskctl.sqlite3")
                    self.assertEqual(
                        route_cache._cache_path(),
                        workspace.resolve() / ".claude" / "task-plans" / "route-cache.json",
                    )
                finally:
                    os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
