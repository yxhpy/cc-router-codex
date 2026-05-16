# Project Context And ADRs

`CONTEXT.md` and `docs/adr/` are optional project-owned documentation surfaces.
They help workers align on vocabulary and decisions without forcing every
project to adopt a heavy process.

## CONTEXT.md

Use `CONTEXT.md` when a project benefits from shared language:

- project purpose and target users
- domain vocabulary and user-facing terms
- naming conventions that should stay consistent
- links to authoritative product, API, or design docs
- terms or assumptions that should not be rediscovered in every session

Workers read `CONTEXT.md` when it exists before choosing domain terms, naming,
or user-facing language. If the file is absent, workers continue from repository
evidence.

## docs/adr/

Use `docs/adr/` for hard-to-reverse decisions such as persistence, APIs,
deployment shape, third-party dependencies, data ownership, or naming that
affects several modules.

Recommended ADR file name:

```text
docs/adr/YYYY-MM-DD-short-title.md
```

Recommended sections:

```markdown
# ADR: Decision Title

## Status

## Context

## Decision

## Consequences

## Alternatives Considered

## Verification Evidence
```

Workers check relevant ADRs when `docs/adr/` exists and the task touches
architecture, persistence, APIs, deployment, dependency choices, storage, or
hard-to-reverse naming decisions. If no ADR applies, workers continue from
current code and task evidence.

## Creation Rules

The installer does not create `CONTEXT.md` or `docs/adr/`. A worker must not
create or rewrite them as a side effect of unrelated work.

Create or update these files only when the user explicitly asks for project
context documentation, a glossary, an ADR, or an architecture decision record.
Use the `docs` role for documentation-only changes and record an artifact that
summarizes what changed.
