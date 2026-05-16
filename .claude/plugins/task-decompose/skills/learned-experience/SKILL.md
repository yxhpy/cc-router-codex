---
name: learned-experience
description: Search and apply compact lessons from completed taskctl/Codex worker runs. Use when starting, planning, implementing, testing, reviewing, closing, or repairing single-step task-control and Codex worker capabilities.
---

# Learned Experience

Use this skill as a retrieval entrypoint, not as a rulebook.

1. Search SQLite first:

```bash
python .claude/scripts/taskctl.py experience-list --query "<term>" --status accepted --json
```

2. If SQLite is unavailable or you need a compact offline index, read
   `references/experience-index.md`.
3. Treat lessons as evidence-backed hints. Current repository code, task
   artifacts, tests, and official docs override old lessons.
4. If a lesson is wrong, stale, duplicate, or too broad, reject or supersede it
   through `taskctl.py` and rerun `experience-sync-skill`.
5. Prefer high-confidence atom metadata for broad reuse. Use
   `experience-sync-skill --min-confidence 4` when generating indexes that
   should hide weak accepted lessons.
