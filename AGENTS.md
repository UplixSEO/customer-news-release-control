<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional
just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review
  items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior.
  Preserve complete failure output.

OpenWiki refreshes are release-driven through `/ship-uplix-release`; the GitHub Actions workflow is `workflow_dispatch`-only for explicit manual runs. Do not add a scheduled or cron trigger.
Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer
updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
