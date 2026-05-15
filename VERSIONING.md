# Version Standard

This repository treats bundled control-plane files and external runtime helpers
as separate version surfaces.

## image-2-prompt MCP

The image prompt-template MCP is versioned by the exact git commit SHA resolved
from:

- repo: `ASSETGEN_PROMPT_MCP_REPO` (default `https://github.com/yxhpy/image-2-prompt`)
- ref: `ASSETGEN_PROMPT_MCP_REF` (default `main`)

Local install state is written under the target prompt-searcher directory:

- `.cc-router-mcp-version.json`: installed commit and version standard.
- `.cc-router-mcp-latest.json`: latest known remote commit and check time.
- `.cc-router-mcp-ready.json`: smoke-test readiness, file fingerprint, and
  version summary.

Every prompt-template check reads the local version first and compares it with
the latest known commit. A remote latest check is refreshed by `ensure`,
`check --refresh-version`, or `version --refresh`; normal generation uses the
latest cache while it is fresh so image creation is not slowed by repeated
network probes.

If the installed commit is unknown or differs from latest, the tool must warn:
the user decides whether to upgrade. Upgrade is explicit:

```powershell
python .claude\scripts\prompt_template_mcp.py ensure --workspace . --refresh-version --upgrade --json
```

No automatic upgrade should happen during normal image generation.
