#!/usr/bin/env python3
"""Unit tests for codex_exec.py environment handling."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import codex_exec


class CodexExecEnvTests(unittest.TestCase):
    def test_codex_proxy_populates_standard_proxy_vars(self) -> None:
        env = {"CODEX_PROXY": "http://127.0.0.1:7890"}
        names = codex_exec._apply_proxy_env(env)

        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            self.assertEqual(env[name], "http://127.0.0.1:7890")
            self.assertIn(name, names)
        self.assertNotIn("NO_PROXY", env)

    def test_existing_proxy_is_mirrored_without_override(self) -> None:
        env = {
            "HTTP_PROXY": "http://explicit-http:8080",
            "https_proxy": "http://lower-https:8080",
            "CODEX_PROXY": "http://fallback:8080",
        }
        codex_exec._apply_proxy_env(env)

        self.assertEqual(env["HTTP_PROXY"], "http://explicit-http:8080")
        self.assertEqual(env["http_proxy"], "http://explicit-http:8080")
        self.assertEqual(env["HTTPS_PROXY"], "http://lower-https:8080")
        self.assertEqual(env["https_proxy"], "http://lower-https:8080")
        self.assertEqual(env["ALL_PROXY"], "http://fallback:8080")

    def test_no_proxy_is_mirrored(self) -> None:
        env = {"no_proxy": "localhost,127.0.0.1"}
        names = codex_exec._apply_proxy_env(env)

        self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1")
        self.assertEqual(env["no_proxy"], "localhost,127.0.0.1")
        self.assertIn("NO_PROXY", names)

    def test_xhigh_reasoning_is_preserved_for_current_cli(self) -> None:
        effort, note = codex_exec._normalize_reasoning_effort("xhigh")

        self.assertEqual(effort, "xhigh")
        self.assertEqual(note, "")

    def test_env_reasoning_effort_overrides_config(self) -> None:
        with mock.patch.object(codex_exec, "_read_config_reasoning_effort", return_value="xhigh"):
            effort, note = codex_exec._reasoning_effort_override({"CODEX_REASONING_EFFORT": "medium"})

        self.assertEqual(effort, "medium")
        self.assertEqual(note, "")

    def test_cli_reasoning_effort_overrides_env_and_config(self) -> None:
        with mock.patch.object(codex_exec, "_read_config_reasoning_effort", return_value="xhigh"):
            effort, note = codex_exec._reasoning_effort_override(
                {"CODEX_REASONING_EFFORT": "medium"},
                "low",
            )

        self.assertEqual(effort, "low")
        self.assertEqual(note, "")

    def test_windows_workspace_mode_avoids_broken_codex_sandbox(self) -> None:
        with mock.patch.object(codex_exec.sys, "platform", "win32"):
            sandbox_mode, note = codex_exec._resolve_sandbox_mode("workspace")

        self.assertEqual(sandbox_mode, "danger-full-access")
        self.assertIn("workspace-write", note)

    def test_cli_help_exposes_model_and_proxy_controls(self) -> None:
        parser_text = codex_exec.__doc__

        self.assertIn("CODEX_PROXY", parser_text)
        self.assertIn("CODEX_MODEL", parser_text)
        self.assertIn("--reasoning-effort", parser_text)

    def test_find_codex_prefers_path_resolution(self) -> None:
        completed = mock.Mock(returncode=0)
        with mock.patch.object(codex_exec.shutil, "which", side_effect=lambda name: "C:/node/codex.cmd" if name == "codex" else None):
            with mock.patch.object(codex_exec.subprocess, "run", return_value=completed) as run:
                self.assertEqual(codex_exec._find_codex(), "C:/node/codex.cmd")

        run.assert_called_with(["C:/node/codex.cmd", "--version"], capture_output=True, text=True, timeout=10)

    def test_codex_command_reads_worker_prompt_from_stdin(self) -> None:
        cmd = codex_exec._build_codex_command(
            "codex",
            "workspace-write",
            workspace="C:/repo",
            model_override="gpt-5.5",
            reasoning_effort="medium",
        )

        self.assertEqual(cmd[-1], "-")
        self.assertNotIn("worker\nprompt", cmd)
        self.assertIn("-C", cmd)
        self.assertIn("C:/repo", cmd)

    def test_codex_stdio_writes_directly_to_log_file(self) -> None:
        handle = object()
        kwargs = codex_exec._codex_stdio_kwargs(handle)

        self.assertIs(kwargs["stdout"], handle)
        self.assertEqual(kwargs["stderr"], codex_exec.subprocess.STDOUT)
        self.assertNotIn("capture_output", kwargs)
        self.assertEqual(kwargs["encoding"], "utf-8")

    def test_worker_prompt_gets_bounded_execution_contract(self) -> None:
        prompt = codex_exec._append_execution_contract("Implement the page.")

        self.assertIn("BOUNDED WORKER EXECUTION CONTRACT", prompt)
        self.assertIn("Do not run\nlong-lived servers in the foreground", prompt)
        self.assertIn("Start-Process", prompt)
        self.assertIn("nohup", prompt)

    def test_foreground_server_patterns_are_detected(self) -> None:
        reason = codex_exec._foreground_server_reason("  Local:   http://localhost:5173/")

        self.assertEqual(reason, "local:   http://")

    def test_foreground_server_guard_seconds_can_be_disabled(self) -> None:
        with mock.patch.dict(codex_exec.os.environ, {"CODEX_FOREGROUND_SERVER_GUARD_SECONDS": "0"}):
            self.assertEqual(codex_exec._foreground_server_guard_seconds(), 0)

    def test_foreground_server_guard_mode_defaults_to_warn(self) -> None:
        with mock.patch.dict(codex_exec.os.environ, {}, clear=True):
            self.assertEqual(codex_exec._foreground_server_guard_mode(), "warn")

    def test_foreground_server_watchdog_warns_without_terminating_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "codex.log"
            with log_path.open("w", encoding="utf-8", errors="replace") as log_f:
                code, message = codex_exec._run_codex_with_watchdog(
                    [
                        sys.executable,
                        "-c",
                        "import time; print('Local:   http://localhost:5173/', flush=True); time.sleep(2)",
                    ],
                    prompt="",
                    env=os.environ.copy(),
                    log_file=log_path,
                    log_file_handle=log_f,
                    guard_seconds=1,
                    guard_mode="warn",
                )
            log_text = log_path.read_text(encoding="utf-8", errors="replace")

        self.assertEqual(code, 0)
        self.assertEqual(message, "")
        self.assertIn("FOREGROUND_SERVER_WARNING", log_text)

    def test_foreground_server_watchdog_terminates_ready_process_when_kill_mode_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "codex.log"
            with log_path.open("w", encoding="utf-8", errors="replace") as log_f:
                code, message = codex_exec._run_codex_with_watchdog(
                    [
                        sys.executable,
                        "-c",
                        "import time; print('Local:   http://localhost:5173/', flush=True); time.sleep(30)",
                    ],
                    prompt="",
                    env=os.environ.copy(),
                    log_file=log_path,
                    log_file_handle=log_f,
                    guard_seconds=1,
                    guard_mode="kill",
                )

        self.assertEqual(code, 124)
        self.assertIn("FOREGROUND_SERVER_BLOCKED", message)

    def test_windows_sandbox_error_is_fatal_pattern(self) -> None:
        reason = codex_exec._fatal_log_reason(
            "2026-05-14T01:47:53.800794Z ERROR codex_core::exec: "
            "exec error: windows sandbox: CreateProcessWithLogonW failed: 1326"
        )

        self.assertIn("fatal worker log pattern", reason)

    def test_historical_sandbox_text_is_not_fatal_pattern(self) -> None:
        reason = codex_exec._fatal_log_reason(
            ".\\logs\\codex\\old.log:2026-05-14T01:47:53Z ERROR codex_core::exec: "
            "exec error: windows sandbox: CreateProcessWithLogonW failed: 1326"
        )

        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
