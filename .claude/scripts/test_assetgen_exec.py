#!/usr/bin/env python3
"""Unit tests for the Codex-only asset generator wrapper."""

from __future__ import annotations

from contextlib import closing
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import assetgen_exec


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"codex-test-png"


class AssetgenExecTests(unittest.TestCase):
    def test_rejects_svg_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with self.assertRaises(assetgen_exec.AssetgenError) as rejected:
                assetgen_exec.output_images(workspace, ["assets/generated/icon.svg"])

        self.assertIn("raster files", str(rejected.exception))

    def test_generates_png_and_manifest_through_codex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            output_rel = "assets/generated/hero.png"
            manifest_rel = "assets/generated/manifest.json"
            output_path = workspace / output_rel
            task_db = workspace / "taskctl.sqlite3"
            old_updated_at = "2000-01-01T00:00:00Z"
            with closing(sqlite3.connect(task_db)) as conn:
                conn.execute(
                    "CREATE TABLE events("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "job_id INTEGER, task_id INTEGER, type TEXT, message TEXT, created_at TEXT)"
                )
                conn.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, updated_at TEXT NOT NULL)")
                conn.execute("INSERT INTO tasks(id, updated_at) VALUES (?, ?)", (7, old_updated_at))
                conn.commit()

            def fake_run(command, **kwargs):
                self.assertEqual(command[0], "codex")
                self.assertIn("--output-schema", command)
                self.assertIn("--output-last-message", command)
                self.assertIn("--add-dir", command)
                self.assertIn("--model", command)
                self.assertIn("gpt-5.4-mini", command)
                self.assertIn("Do not create SVG", kwargs["input"])
                self.assertIn("Retrieved prompt-template context:", kwargs["input"])
                self.assertIn("Template context from MCP", kwargs["input"])
                message_path = Path(command[command.index("--output-last-message") + 1])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(PNG_BYTES)
                message_path.write_text(
                    json.dumps(
                        {
                            "images": [
                                {
                                    "index": 1,
                                    "path": str(output_path),
                                    "format": "png",
                                    "description": "test image",
                                }
                            ],
                            "notes": "ok",
                        }
                    ),
                    encoding="utf-8",
                )
                return mock.Mock(returncode=0, stdout="", stderr="")

            prompt_context = {
                "text": "Template context from MCP",
                "metadata": {"query": "type:template hero", "template_ids": ["template:hero"]},
            }
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "TASKCTL_DB": str(task_db),
                        "TASKCTL_JOB_ID": "3",
                        "TASKCTL_TASK_ID": "7",
                    },
                ),
                mock.patch.object(assetgen_exec.subprocess, "run", side_effect=fake_run),
                mock.patch.object(assetgen_exec.prompt_template_mcp, "build_asset_prompt_context", return_value=prompt_context),
            ):
                code = assetgen_exec.main(
                    [
                        "--workspace",
                        str(workspace),
                        "--prompt",
                        "Create a web hero image.",
                        "--output",
                        output_rel,
                        "--manifest",
                        manifest_rel,
                        "--asset-role",
                        "web",
                        "--codex-bin",
                        "codex",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(output_path.read_bytes(), PNG_BYTES)
            manifest = json.loads((workspace / manifest_rel).read_text(encoding="utf-8"))
            self.assertEqual(manifest["backend"], "codex")
            self.assertEqual(manifest["images"][0]["path"], output_rel)
            self.assertEqual(manifest["prompt_template_mcp"]["template_ids"], ["template:hero"])
            with closing(sqlite3.connect(task_db)) as conn:
                rows = conn.execute("SELECT type, message, job_id, task_id FROM events ORDER BY id").fetchall()
                updated_at = conn.execute("SELECT updated_at FROM tasks WHERE id = 7").fetchone()[0]

            self.assertNotEqual(updated_at, old_updated_at)
            self.assertTrue(all(row[0] == "assetgen_progress" for row in rows))
            self.assertTrue(all(row[2:] == (3, 7) for row in rows))
            messages = [row[1] for row in rows]
            self.assertTrue(any("requested 1 raster image(s)" in message for message in messages))
            self.assertTrue(any("codex generation started" in message for message in messages))
            self.assertTrue(any("verified image 1/1" in message for message in messages))
            self.assertTrue(any("wrote manifest" in message for message in messages))
            self.assertTrue(any("assetgen complete" in message for message in messages))

    def test_fast_mode_skips_prompt_template_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            output_rel = "assets/generated/hero.png"
            output_path = workspace / output_rel

            def fake_run(command, **kwargs):
                self.assertIn("Fast generation contract:", kwargs["input"])
                self.assertIn("Prompt-template MCP skipped", kwargs["input"])
                message_path = Path(command[command.index("--output-last-message") + 1])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(PNG_BYTES)
                message_path.write_text(
                    json.dumps(
                        {
                            "images": [
                                {
                                    "index": 1,
                                    "path": str(output_path),
                                    "format": "png",
                                    "description": "fast test image",
                                }
                            ],
                            "notes": "ok",
                        }
                    ),
                    encoding="utf-8",
                )
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(assetgen_exec.subprocess, "run", side_effect=fake_run),
                mock.patch.object(assetgen_exec.prompt_template_mcp, "build_asset_prompt_context") as prompt_context,
            ):
                code = assetgen_exec.main(
                    [
                        "--workspace",
                        str(workspace),
                        "--prompt",
                        "Create a web hero image.",
                        "--output",
                        output_rel,
                        "--asset-role",
                        "web",
                        "--codex-bin",
                        "codex",
                        "--fast",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertFalse(prompt_context.called)
            self.assertEqual(output_path.read_bytes(), PNG_BYTES)

    def test_reuse_existing_valid_output_skips_codex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            output_rel = "assets/generated/hero.png"
            manifest_rel = "assets/generated/manifest.json"
            output_path = workspace / output_rel
            output_path.parent.mkdir(parents=True)
            output_path.write_bytes(PNG_BYTES)

            with (
                mock.patch.object(assetgen_exec.subprocess, "run") as codex_run,
                mock.patch.object(assetgen_exec.prompt_template_mcp, "build_asset_prompt_context") as prompt_context,
            ):
                code = assetgen_exec.main(
                    [
                        "--workspace",
                        str(workspace),
                        "--prompt",
                        "Create a web hero image.",
                        "--output",
                        output_rel,
                        "--manifest",
                        manifest_rel,
                        "--reuse-existing",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertFalse(codex_run.called)
            self.assertFalse(prompt_context.called)
            manifest = json.loads((workspace / manifest_rel).read_text(encoding="utf-8"))
            self.assertEqual(manifest["images"][0]["path"], output_rel)
            self.assertEqual(manifest["prompt_template_mcp"]["mode"], "reuse_existing")


if __name__ == "__main__":
    unittest.main()
