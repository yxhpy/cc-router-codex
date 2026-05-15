#!/usr/bin/env python3
"""Unit tests for image prompt-template MCP bootstrap and retrieval."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import prompt_template_mcp


def make_fake_prompt_searcher(target: Path) -> None:
    (target / "data").mkdir(parents=True)
    (target / "src" / "prompt_searcher").mkdir(parents=True)
    if prompt_template_mcp.os.name == "nt":
        python_path = target / ".venv" / "Scripts" / "python.exe"
    else:
        python_path = target / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("python", encoding="utf-8")
    (target / "install.json").write_text(
        json.dumps({"profile": "full", "python": str(python_path)}),
        encoding="utf-8",
    )
    for relative in (
        "data/search-docs.json",
        "data/search-index.json",
        "data/search-facets.json",
        "src/prompt_searcher/mcp_server.py",
    ):
        (target / relative).write_text("{}", encoding="utf-8")
    prompt_template_mcp.write_version_file(
        target,
        repo=prompt_template_mcp.DEFAULT_REPO,
        ref=prompt_template_mcp.DEFAULT_REF,
        installed_commit="a" * 40,
        latest_commit="a" * 40,
    )
    prompt_template_mcp.write_latest_cache(
        target,
        repo=prompt_template_mcp.DEFAULT_REPO,
        ref=prompt_template_mcp.DEFAULT_REF,
        latest_commit="a" * 40,
        source="test",
    )


def make_partial_full_prompt_searcher(target: Path) -> None:
    (target / "data").mkdir(parents=True)
    (target / "src" / "prompt_searcher").mkdir(parents=True)
    if prompt_template_mcp.os.name == "nt":
        python_path = target / ".venv" / "Scripts" / "python.exe"
    else:
        python_path = target / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("python", encoding="utf-8")
    for relative in (
        "data/search-docs.json",
        "data/search-index.json",
        "data/search-facets.json",
        "src/prompt_searcher/mcp_server.py",
    ):
        (target / relative).write_text("{}", encoding="utf-8")


class PromptTemplateMcpTests(unittest.TestCase):
    def test_quick_check_uses_ready_marker_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / ".prompt-searcher"
            make_fake_prompt_searcher(target)
            fingerprint = prompt_template_mcp.install_fingerprint(target)
            (target / prompt_template_mcp.READY_FILE).write_text(
                json.dumps({"schemaVersion": 1, "ok": True, "fingerprint": fingerprint}),
                encoding="utf-8",
            )

            status = prompt_template_mcp.quick_check(workspace)

        self.assertTrue(status.ok)
        self.assertTrue(status.fast)
        self.assertEqual(status.reason, "cached MCP readiness")
        self.assertTrue(status.version.is_latest if status.version else False)

    def test_ensure_installs_when_missing_then_writes_ready_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / ".prompt-searcher"

            def fake_install(workspace_arg, target_arg=None, **_kwargs):
                make_fake_prompt_searcher(prompt_template_mcp.resolve_target(workspace_arg, target_arg))
                return {"target": str(target)}

            with (
                mock.patch.object(prompt_template_mcp, "install_prompt_mcp", side_effect=fake_install) as install_mock,
                mock.patch.object(prompt_template_mcp, "mcp_smoke_test", return_value={"topId": "template:poster"}),
                mock.patch.object(prompt_template_mcp, "remote_latest_commit", return_value="a" * 40),
            ):
                status = prompt_template_mcp.ensure_prompt_mcp(workspace)

            marker = target / prompt_template_mcp.READY_FILE
            marker_exists = marker.exists()

        self.assertTrue(status.ok)
        self.assertFalse(status.fast)
        self.assertTrue(marker_exists)
        install_mock.assert_called_once()

    def test_install_recovers_when_full_payload_exists_but_powershell_smoke_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / ".prompt-searcher"

            def fake_run(command, **_kwargs):
                if "install.py" in " ".join(str(item) for item in command):
                    make_partial_full_prompt_searcher(target)
                    return mock.Mock(returncode=1, stdout="", stderr="FileNotFoundError: powershell")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(prompt_template_mcp.subprocess, "run", side_effect=fake_run),
                mock.patch.object(prompt_template_mcp, "local_clone_commit", return_value="b" * 40),
                mock.patch.object(prompt_template_mcp, "mcp_smoke_test", return_value={"topId": "template:poster"}),
            ):
                result = prompt_template_mcp.install_prompt_mcp(workspace)

            install_record = json.loads((target / "install.json").read_text(encoding="utf-8"))
            version = json.loads((target / prompt_template_mcp.VERSION_FILE).read_text(encoding="utf-8"))

        self.assertEqual(result["installed_commit"], "b" * 40)
        self.assertEqual(install_record["profile"], "full")
        self.assertIn("powershell", install_record["smokeTest"]["recoveredFrom"].lower())
        self.assertEqual(version["installedCommit"], "b" * 40)

    def test_version_status_warns_when_installed_commit_is_behind_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".prompt-searcher"
            make_fake_prompt_searcher(target)
            prompt_template_mcp.write_version_file(
                target,
                repo=prompt_template_mcp.DEFAULT_REPO,
                ref=prompt_template_mcp.DEFAULT_REF,
                installed_commit="1" * 40,
                latest_commit="1" * 40,
            )
            prompt_template_mcp.write_latest_cache(
                target,
                repo=prompt_template_mcp.DEFAULT_REPO,
                ref=prompt_template_mcp.DEFAULT_REF,
                latest_commit="2" * 40,
                source="test",
            )
            fingerprint = prompt_template_mcp.install_fingerprint(target)
            (target / prompt_template_mcp.READY_FILE).write_text(
                json.dumps({"schemaVersion": 1, "ok": True, "fingerprint": fingerprint}),
                encoding="utf-8",
            )

            status = prompt_template_mcp.quick_check(Path(tmp))

        self.assertTrue(status.ok)
        self.assertTrue(status.version.upgrade_available if status.version else False)
        self.assertIn("upgrade available", status.reason)

    def test_build_asset_prompt_context_fetches_template_details(self) -> None:
        search_payload = {
            "results": [
                {"document": {"id": "template:hero", "title": "Hero Template", "type": "template"}},
            ]
        }
        prompt_payload = {
            "document": {
                "id": "template:hero",
                "title": "Hero Template",
                "type": "template",
                "fields": {
                    "intent": "Use for web hero imagery.",
                    "body": "Lock subject, palette, lighting, and aspect ratio.",
                    "negative": "Avoid text clutter.",
                },
                "facets": {"category": "Web", "styles": ["UI"], "scenes": ["Tech"], "tags": ["Hero"]},
            }
        }
        status = prompt_template_mcp.PromptMcpStatus(True, True, True, "T", "cached MCP readiness", 0.1)

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(prompt_template_mcp, "ensure_prompt_mcp", return_value=status),
            mock.patch.object(prompt_template_mcp, "mcp_call_tools", side_effect=[[search_payload], [prompt_payload]]) as calls,
        ):
            context = prompt_template_mcp.build_asset_prompt_context(
                Path(tmp),
                "Create a clean SaaS hero image.",
                asset_role="web",
                size="1024x1024",
                style="quiet dashboard",
            )

        self.assertIn("template:hero", context["text"])
        self.assertIn("Lock subject", context["text"])
        self.assertIn("Avoid text clutter", context["text"])
        self.assertEqual(context["metadata"]["template_ids"], ["template:hero"])
        search_call = calls.call_args_list[0].args[1][0]
        self.assertEqual(search_call[0], "search_prompts")
        self.assertIn("type:template", search_call[1]["query"])


if __name__ == "__main__":
    unittest.main()
