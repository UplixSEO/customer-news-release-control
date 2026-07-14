# Customer News release control

This public repository is the protected production approval boundary for the
private Customer News source repository on GitHub Free. It contains no Customer
News source, customer identifiers, build payloads, runtime artifacts, or
credentials.

The private repository's successful `main` guard creates an annotated tag named
`customer-news-release/<run-id>-(promote|rollback)-<40-hex-sha>`. A reviewer dispatches
`promote.yml` with that exact tag. The production environment releases a
read-only GitHub App key; the workflow verifies the private tag and successful
guard run plus the unique merged `dev`-to-`main` PR for that SHA, exchanges
OIDC for the approver-only GCP identity, then approves
exactly 17 already-pending fixed Cloud Build triggers. It cannot create builds,
invoke or edit triggers, upload source, impersonate the build service account,
or cancel builds.

The exact-batch waiter distinguishes in-flight builds from terminal failures.
It continues polling while any expected authority is nonterminal, but exits
immediately once an exact 17-build batch is terminal with any non-success
result; a failed batch is never allowed to consume the full workflow timeout.

For a least-privilege audit without a release, dispatch the same protected
workflow with `authority_probe=true` and any syntactically inert `release_tag`
input. After the production reviewer approves the environment, the probe
verifies the App's exact one-repository/read-only boundary and the federated
identity's effective permissions. Candidate validation and the 17-build
approval step are skipped in this mode, so no build, release, or deployment is
started.

The controller also carries fail-closed WIF denial checks. Setting
`nonproduction_wif_probe=true` on `promote.yml` proves that the exact trusted
workflow cannot exchange outside the `production` environment. The separate
`wif-negative.yml` proves that a different workflow is denied even after the
production reviewer gate and that pull-request claims are denied. These jobs
only invoke the native OIDC authentication action and assert its failure; they
contain no GitHub, Cloud Build, IAM, or storage mutation commands.

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

## Release ledger contract

Production convergence uses GitHub Deployments in this public repository as
the native append-only ledger. The deterministic decision helper in
`scripts/release_ledger.py` never calls an API: it consumes normalized
Deployment state and upstream ancestry proof, then returns `create`, `resume`,
`already_succeeded`, or `superseded`. An interrupted release resumes the same
Deployment ID; an older candidate becomes inactive only after a proven
descendant succeeds. Rollback creates a new release-tag epoch and is accepted
only for a compatibility-approved ancestor with a non-empty reason.
