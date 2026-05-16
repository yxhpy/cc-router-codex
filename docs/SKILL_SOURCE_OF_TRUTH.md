# Skill Source Of Truth

Bundled skills are governed by `.claude/skill-manifest.json`.

The manifest is the source of truth for:

- which skills are distributable
- where each skill source directory lives
- which bridge directories publish that skill to Claude or plugin surfaces
- which skills are draft, deprecated, or private and must not be published

## Buckets

`distributable`
: Published in `.claude/skills/<name>` and, when needed, mirrored to a plugin
bridge such as `.claude/plugins/<plugin>/skills/<name>`.

`draft`
: Work in progress. Draft skills must not appear under bundled published
surfaces.

`deprecated`
: Retained for history or migration notes only. Deprecated skills must not be
published.

`private`
: Local/private-only skill material. Private skills must not be published.

## Bridge Rules

Claude bridge paths are deterministic:

```text
.claude/skills/<skill-name>
```

Plugin bridge paths are deterministic:

```text
.claude/plugins/<plugin-name>/skills/<skill-name>
```

For a distributable skill, every bridge must contain `SKILL.md`, the frontmatter
`name` must match the manifest name, and mirrored bridge content must match the
source directory byte-for-byte.

## Verification

Run:

```sh
python tools/skill_manifest_check.py
```

Installed targets can run the installed copy directly:

```sh
python .claude/scripts/skill_manifest_check.py
```

The full verification gate also runs this checker:

```sh
python -B .claude/scripts/test_all.py
```

The checker fails when:

- a published skill is missing from the manifest
- a draft, deprecated, or private skill appears in a published bridge
- a bridge path is not deterministic
- bridge frontmatter names drift from the manifest
- plugin bridge contents differ from the source directory

## Updating Skills

When adding a distributable skill:

1. Add or update its source directory.
2. Add one manifest entry with bucket `distributable`.
3. List every Claude/plugin bridge path.
4. Copy or regenerate bridge content from the source directory.
5. Run `python tools/skill_manifest_check.py`.

When drafting or deprecating a skill, keep it outside `.claude/skills/` and
`.claude/plugins/*/skills/`, then record the appropriate bucket in the manifest.
