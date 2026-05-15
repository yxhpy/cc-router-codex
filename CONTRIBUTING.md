# Contributing

Changes should preserve the control-plane contract: Claude orchestrates, Codex
executes bounded production work, and deliverables are written to disk with
evidence.

## Local Verification

Run the full gate before opening a pull request:

```powershell
python -B .claude\scripts\test_all.py
```

Optional host-real checks:

```powershell
python -B .claude\scripts\test_all.py --real-codex
python -B .claude\scripts\test_all.py --real-claude-cli
```

## Change Guidelines

- Keep runtime state out of commits: `.claude/artifacts`, `.claude/task-plans`,
  `.claude/.env`, SQLite files, logs, and `.prompt-searcher`.
- Keep hook behavior hard-enforced in scripts and tests; do not rely only on
  prompt instructions.
- Keep installer paths portable and independent of the caller's working
  directory.
- Add focused tests when changing hooks, routing, model policy, installer
  rewriting, MCP integration, or role/artifact contracts.
- Update `CHANGELOG.md` for user-visible behavior.

## Release Guidelines

Repository versions follow SemVer and must match `VERSION`.

- Patch: docs, tests, small guard hardening, or installer fixes that preserve
  the public control-plane contract.
- Minor: new roles, hooks, commands, runtime integrations, or stricter behavior.
- Major: incompatible command, artifact, hook, install, or policy changes.

Tags must use `vX.Y.Z`.
