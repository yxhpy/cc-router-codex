#!/usr/bin/env python3
"""Machine-readable command contracts for Claude/Codex control-plane commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_paths import command_arg, python_command, script_path


def _workspace_arg(workspace: str | None) -> str:
    value = str(workspace or Path.cwd()).strip()
    return '"' + value.replace('"', '\\"') + '"'


def _script(name: str) -> str:
    return command_arg(str(script_path(name)))


@dataclass(frozen=True)
class CommandContract:
    name: str
    summary: str
    command: str
    writes: str
    use_when: str
    failure_hint: str
    examples: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "command": self.command,
            "writes": self.writes,
            "use_when": self.use_when,
            "failure_hint": self.failure_hint,
            "examples": list(self.examples),
        }


def contracts(workspace: str | None = None) -> dict[str, CommandContract]:
    taskctl = f"{python_command()} {_script('taskctl.py')}"
    focus = f"{python_command()} {_script('focus_guard.py')}"
    workspace_value = _workspace_arg(workspace)
    return {
        "capability": CommandContract(
            name="capability",
            summary="Run exactly one bounded Codex worker capability through taskctl.",
            command=(
                f"{taskctl} capability --role <role> --title \"<title>\" "
                f"--prompt \"<bounded worker prompt>\" --artifact <kind:path> "
                f"--workspace {workspace_value} --goal \"<user goal>\""
            ),
            writes="Writes only through taskctl/Codex worker under the target workspace and task database.",
            use_when="Use instead of direct Write/Edit/Bash product-file edits.",
            failure_hint="Run `taskctl doctor --json` and inspect taskctl status/audit before retrying.",
            examples=(
                f"{taskctl} capability --role fullstack --title \"Implement bounded fix\" --prompt \"Implement one bounded fix and record implementation_summary.\" --artifact implementation_summary:.claude/artifacts/implementation_summary.md --workspace {workspace_value} --goal \"Implement bounded fix\"",
            ),
        ),
        "status": CommandContract(
            name="status",
            summary="Show recent jobs or one job in the taskctl database.",
            command=f"{taskctl} status [job_id]",
            writes="Read-only.",
            use_when="Use after a capability returns or fails.",
            failure_hint="Run `taskctl doctor --json` to verify database and script paths.",
            examples=(f"{taskctl} status", f"{taskctl} status 1 --json"),
        ),
        "audit": CommandContract(
            name="audit",
            summary="Audit whether a job is complete and all required artifacts exist.",
            command=f"{taskctl} audit <job_id>",
            writes="Read-only.",
            use_when="Use after a capability creates expected artifacts.",
            failure_hint="Run status first to find the correct job id.",
            examples=(f"{taskctl} audit 1", f"{taskctl} audit 1 --json"),
        ),
        "focus-complete": CommandContract(
            name="focus-complete",
            summary="Mark the active production focus complete with evidence.",
            command=f"{focus} complete --workspace {workspace_value} --evidence \"<evidence>\"",
            writes="Updates .claude/task-plans/focus_state.json runtime state.",
            use_when="Use only after the requested goal is genuinely complete.",
            failure_hint="Run focus-status and taskctl audit to gather evidence.",
            examples=(f"{focus} complete --workspace {workspace_value} --evidence \"tests passed and artifact exists\"",),
        ),
        "focus-exhausted": CommandContract(
            name="focus-exhausted",
            summary="Mark the active production focus exhausted with attempted routes and blocker evidence.",
            command=f"{focus} exhausted --workspace {workspace_value} --evidence \"<attempts and blocker>\"",
            writes="Updates .claude/task-plans/focus_state.json runtime state.",
            use_when="Use only after viable routes were attempted and the blocker is concrete.",
            failure_hint="Record the failed commands and exact blocker in the evidence.",
            examples=(f"{focus} exhausted --workspace {workspace_value} --evidence \"attempted route A/B; blocked by missing credential\"",),
        ),
        "focus-status": CommandContract(
            name="focus-status",
            summary="Show current focus guard state.",
            command=f"{focus} status --workspace {workspace_value}",
            writes="Read-only.",
            use_when="Use when Stop hook reports unfinished focus.",
            failure_hint="Check that the workspace path matches the Claude session target project.",
            examples=(f"{focus} status --workspace {workspace_value}",),
        ),
        "doctor": CommandContract(
            name="doctor",
            summary="Print control-plane command and environment diagnostics.",
            command=f"{taskctl} doctor --workspace {workspace_value}",
            writes="Read-only.",
            use_when="Use after command syntax failures, hook rejections, or unclear local paths.",
            failure_hint="If doctor fails, run the absolute Python command shown by SessionStart.",
            examples=(f"{taskctl} doctor --workspace {workspace_value} --json",),
        ),
        "checkpoint-save": CommandContract(
            name="checkpoint-save",
            summary="Save a resumable checkpoint for a taskctl job.",
            command=f"{taskctl} checkpoint-save --job-id <job_id> --title \"<checkpoint title>\"",
            writes="Writes a checkpoint snapshot under .claude/task-plans/checkpoints and records it in SQLite.",
            use_when="Use after failed, blocked, or incomplete work so the next turn can resume without rediscovering state.",
            failure_hint="Run `taskctl audit <job_id> --json` and `taskctl status <job_id> --json` if checkpoint content looks incomplete.",
            examples=(f"{taskctl} checkpoint-save --job-id 1 --title \"Resume failed asset generation\"",),
        ),
        "checkpoint-list": CommandContract(
            name="checkpoint-list",
            summary="List resumable checkpoints.",
            command=f"{taskctl} checkpoint-list --workspace {workspace_value}",
            writes="Read-only.",
            use_when="Use before restoring when the latest checkpoint is unclear.",
            failure_hint="Pass --job-id <job_id> to narrow the list to one job.",
            examples=(f"{taskctl} checkpoint-list --workspace {workspace_value} --json",),
        ),
        "checkpoint-restore": CommandContract(
            name="checkpoint-restore",
            summary="Restore one checkpoint by id, or the latest checkpoint if no id is given.",
            command=f"{taskctl} checkpoint-restore [checkpoint_id]",
            writes="Read-only.",
            use_when="Use at the start of a resumed turn after command failures or Stop hook blocks.",
            failure_hint="Run `taskctl checkpoint-list --json` if the checkpoint id is unknown.",
            examples=(f"{taskctl} checkpoint-restore 1 --json",),
        ),
        "checkpoint-report": CommandContract(
            name="checkpoint-report",
            summary="Merge all checkpoints for a job into one report.",
            command=f"{taskctl} checkpoint-report --job-id <job_id>",
            writes="Read-only.",
            use_when="Use before closure or handoff when several failed/retry states should be summarized.",
            failure_hint="Run `taskctl checkpoint-list --job-id <job_id>` to confirm checkpoints exist.",
            examples=(f"{taskctl} checkpoint-report --job-id 1 --json",),
        ),
        "command": CommandContract(
            name="command",
            summary="Print one command contract by name.",
            command=f"{taskctl} command <name> --workspace {workspace_value}",
            writes="Read-only.",
            use_when="Use before running an unfamiliar control-plane command.",
            failure_hint="Run `taskctl command --list` to see available names.",
            examples=(f"{taskctl} command capability --workspace {workspace_value}",),
        ),
        "npm-install": CommandContract(
            name="npm-install",
            summary="Install JavaScript project dependencies.",
            command="npm install",
            writes="Package-manager lifecycle writes dependency and lockfile state.",
            use_when="Use in a project directory containing package.json.",
            failure_hint="Run npm error output through operator role if dependency or platform errors remain.",
            examples=("npm install",),
        ),
        "npm-build": CommandContract(
            name="npm-build",
            summary="Run the project's default npm build script.",
            command="npm run build",
            writes="Package-manager lifecycle may write build output.",
            use_when="Use when package.json defines a build script.",
            failure_hint="Run `npm run` to list scripts if build is missing.",
            examples=("npm run build",),
        ),
        "npm-test": CommandContract(
            name="npm-test",
            summary="Run the project's npm test script.",
            command="npm test",
            writes="Package-manager lifecycle may write test caches or reports.",
            use_when="Use when package.json defines a test script.",
            failure_hint="Run `npm run` to list scripts if test is missing.",
            examples=("npm test",),
        ),
    }


def get_contract(name: str, workspace: str | None = None) -> CommandContract | None:
    return contracts(workspace).get(str(name or "").strip())


def names() -> list[str]:
    return sorted(contracts().keys())


def next_command(name: str, workspace: str | None = None) -> str:
    contract = get_contract(name, workspace)
    return contract.command if contract else contracts(workspace)["doctor"].command


def inspect_command(name: str, workspace: str | None = None) -> str:
    return next_command("command", workspace).replace("<name>", str(name or "capability").strip() or "capability")
