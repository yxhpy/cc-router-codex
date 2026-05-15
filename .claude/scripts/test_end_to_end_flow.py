#!/usr/bin/env python3
"""End-to-end smoke test for single-step taskctl capability execution."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "scripts"
TASKCTL = SCRIPTS / "taskctl.py"
CODEX_EXEC = SCRIPTS / "codex_exec.py"
HOOK = SCRIPTS / "hook_intercept_create.py"
PROMPT_HOOK = SCRIPTS / "hook_user_prompt_submit.py"
SKILL_VALIDATE = Path.home() / ".codex" / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py"
FOCUS_STATE = ROOT / ".claude" / "task-plans" / "focus_state.json"
GOAL = "帮我做一个单步能力测试页面"


def input_guard_mock() -> dict[str, str]:
    return {
        "TASKCTL_INPUT_GUARD_MOCK_JSON": json.dumps(
            {
                "allowed": True,
                "has_action": True,
                "bounded": True,
                "violation": "",
                "suggested_role": "",
                "confidence": 0.9,
            },
            ensure_ascii=False,
        )
    }


def run_command(
    args: list[str],
    *,
    input_text: str | None = None,
    check: bool = True,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env.update(input_guard_mock())
    if env:
        merged_env.update(env)
    result = subprocess.run(
        args,
        input=input_text,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=timeout,
        env=merged_env,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed exit={result.returncode}: {' '.join(args)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def run_taskctl(db: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_command([sys.executable, str(TASKCTL), "--db", str(db), *args], check=check)


def router_mock() -> dict[str, str]:
    return {
        "TASKCTL_ROUTER_MOCK_JSON": json.dumps({
            "production_work": True,
            "role": "fullstack",
            "title": "Create single-step test page",
            "worker_prompt": "Execute one atomic fullstack task and stop.",
            "artifacts": ["html:single_step_test.html"],
            "reason": "mocked LLM route",
            "confidence": 0.9,
        }, ensure_ascii=False)
    }


def run_hook(
    path: Path,
    payload: dict[str, object],
    check: bool = False,
    env: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    result = run_command(
        [sys.executable, str(path)],
        input_text=json.dumps(payload, ensure_ascii=False),
        check=check,
        env=env,
    )
    return result.returncode, json.loads(result.stdout)


class EndToEndFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.db = self.base / "taskctl.sqlite3"
        self.addCleanup(lambda: FOCUS_STATE.unlink() if FOCUS_STATE.exists() else None)

    def test_prompt_to_single_step_audit_flow(self) -> None:
        code, prompt_hook = run_hook(PROMPT_HOOK, {"prompt": GOAL}, env=router_mock())
        self.assertEqual(code, 0)
        prompt_context = prompt_hook["hookSpecificOutput"]["additionalContext"]
        self.assertIn("taskctl.py capability", prompt_context)
        self.assertIn('--artifact "html:single_step_test.html"', prompt_context)
        self.assertNotIn("filter-input --role", prompt_context)
        self.assertNotIn("enqueue <job_id>", prompt_context)
        self.assertIn("Do not use fixed workflows", prompt_context)
        self.assertIn("Hard focus rule", prompt_context)

        code, write_block = run_hook(HOOK, {"tool_name": "Write", "tool_input": {"file_path": "direct.html"}})
        self.assertEqual(code, 2)
        self.assertEqual(write_block["decision"], "block")
        self.assertIn("taskctl.py capability", write_block["reason"])

        submit = json.loads(
            run_taskctl(
                self.db,
                "submit-auto",
                GOAL,
                "--workspace",
                str(self.workspace),
                "--json",
            ).stdout
        )
        job_id = int(submit["job_id"])
        self.assertEqual(submit["workflow"], "atomic")
        self.assertEqual(submit["tasks"], 0)

        run_taskctl(
            self.db,
            "filter-input",
            "--role",
            "fullstack",
            "--title",
            "Create single capability artifact",
            "--prompt",
            "Write one smoke report artifact and record smoke_report.",
            "--required-artifact",
            "smoke_report",
        )
        enqueue = run_taskctl(
            self.db,
            "enqueue",
            str(job_id),
            "--role",
            "fullstack",
            "--title",
            "Create single capability artifact",
            "--prompt",
            "Write one smoke report artifact and record smoke_report.",
            "--required-artifact",
            "smoke_report",
        ).stdout
        task_id = int(enqueue.split()[1])

        dry_run = json.loads(run_taskctl(self.db, "run-next", str(job_id), "--dry-run").stdout)
        self.assertEqual(dry_run["workflow"], "atomic")
        self.assertEqual(dry_run["task_id"], task_id)
        self.assertEqual(dry_run["required_artifacts"], ["smoke_report"])

        artifact_dir = self.workspace / ".claude" / "artifacts" / f"job-{job_id}"
        artifact_dir.mkdir(parents=True)
        relative = f".claude/artifacts/job-{job_id}/smoke_report.md"
        (self.workspace / relative).write_text("ok", encoding="utf-8")
        run_taskctl(self.db, "artifact", str(task_id), "--kind", "smoke_report", "--path", relative, "--summary", "smoke")
        run_taskctl(self.db, "complete-task", str(task_id), "--summary", "simulated one capability completion")

        audit = json.loads(run_taskctl(self.db, "audit", str(job_id), "--json").stdout)
        self.assertTrue(audit["complete"])
        self.assertEqual(audit["workflow"], "atomic")


def codex_smoke() -> None:
    expected = "taskctl-e2e-codex-ok"
    result = run_command(
        [
            sys.executable,
            str(CODEX_EXEC),
            "--skip-safety",
            "-m",
            "readonly",
            f"Print exactly {expected} and do not edit files.",
        ],
        timeout=180,
    )
    if expected not in result.stdout:
        raise AssertionError(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-codex", action="store_true")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EndToEndFlowTests)
    result = unittest.TextTestRunner().run(suite)
    if not result.wasSuccessful():
        return 1
    if args.real_codex:
        codex_smoke()
    if SKILL_VALIDATE.exists():
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
