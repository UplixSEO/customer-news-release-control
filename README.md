# Customer News release control

This public repository is the protected production approval boundary for the
private Customer News source repository on GitHub Free. It contains no Customer
News source, customer identifiers, build payloads, runtime artifacts, or
credentials.

The private repository's successful `main` guard creates an annotated tag named
`customer-news-release/<run-id>-<40-hex-sha>`. A reviewer dispatches
`promote.yml` with that exact tag. The production environment releases a
read-only GitHub App key; the workflow verifies the private tag and successful
guard run plus the unique merged `dev`-to-`main` PR for that SHA, exchanges
OIDC for the approver-only GCP identity, then approves
exactly 17 already-pending fixed Cloud Build triggers. It cannot create builds,
invoke or edit triggers, upload source, impersonate the build service account,
or cancel builds.

Uplix credentials are stored only in Uplix-owned GitHub/GCP control planes.
Léonard's personal 1Password stack is not a dependency or recovery store.

## Local verification

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/test_release_contract.py -q
bash -n scripts/*.sh
```

## Public metadata

The public surface is intentionally limited to generic workflow code, fixed
trigger names, the approved private commit SHA, release timing/status, and the
reviewer recorded by GitHub. It must never publish private source, customer
names or identifiers, Cloud Build payloads, logs, or artifacts.

The private-upstream GitHub App is installed only on `Uplix-Agents` with
metadata, Actions, Contents, and Pull requests set to read-only. Webhooks and
all write permissions remain disabled.
