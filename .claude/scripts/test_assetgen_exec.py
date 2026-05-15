#!/usr/bin/env python3
"""Unit tests for the Codex-only asset generator wrapper."""

from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
