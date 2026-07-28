# Customer News release control

This public repository is the protected production approval boundary for the
private Customer News source repository on GitHub Free. It contains no Customer
News source, customer identifiers, build payloads, runtime artifacts, or
credentials.

The private repository's successful `main` guard creates an annotated tag named
`customer-news-release/<run-id>-(promote|rollback)-<40-hex-sha>`. The scheduled
`auto-promote.yml` poller — or a human, via manual dispatch — dispatches
`promote.yml` with that exact tag, and the `production` environment holds each
release job behind a cancellable wait timer. The production environment
releases a read-only GitHub App key; the workflow verifies the private tag and
successful guard run plus the unique merged `dev`-to-`main` PR for that SHA,
exchanges OIDC for the approver-only GCP identity, then approves
exactly 17 already-pending fixed Cloud Build triggers. It cannot create builds,
invoke or edit triggers, upload source, impersonate the build service account,
or cancel builds.

The exact-batch waiter distinguishes in-flight builds from terminal failures.
It continues polling while any expected authority is nonterminal, but exits
immediately once an exact 17-build batch is terminal with any non-success
result; a failed batch is never allowed to consume the full workflow timeout.
If the workflow itself is interrupted after approval, resuming the same GitHub
Deployment approves only build IDs still in `PENDING`; `QUEUED`, `WORKING`, and
`SUCCESS` IDs are read back and left untouched, so proof repair never causes a
blind redeploy.

A descendant release waits only while a prior exact batch has `QUEUED` or
`WORKING` mutations. An unapproved `PENDING` batch is already quiescent and
cannot block the descendant indefinitely; it remains inert, then receives its
ancestry-proven inactive status only after the descendant proof succeeds.

## Automatic promotion

`auto-promote.yml` polls every 30 minutes and dispatches `promote.yml` when
the private upstream head has a matching promote release tag. The poller only
selects a tag: it resolves the upstream head and its exact `…-promote-<sha>`
tag through the same read-only GitHub App, then asks the deterministic helper
(`release_ledger.py auto-promote`) whether to dispatch. It skips when no
candidate tag exists yet, when the head is already the accepted deployment,
when a successful ledger entry with a newer release epoch pins the runtime —
after a rollback the stale head is never re-promoted; automation resumes only
with the next release tag — and it holds while a promote run is already
queued, waiting, or in progress. Candidate validation stays entirely in
`promote.yml`, so a bad candidate fails exactly as a manual dispatch would.
Because the App key is a production-environment secret, each poll passes the
same environment gate as a release job; the poller never requests OIDC token
authority, so it cannot exchange cloud credentials.

This inverts the release gate's failure mode. The `production` environment
applies a cancellable wait timer instead of a required reviewer: when nobody
acts, a valid candidate now ships after the timer instead of stalling
indefinitely, and cancelling the run inside the wait window is the manual
intervention. Cancelling delays one attempt only — the poller dispatches
again on a later cycle while the candidate remains valid. To hold a release
durably, disable `auto-promote.yml`, roll back (the new epoch pins the
runtime), or land a superseding candidate. The timer applies to the poller,
`promote`, and `prove` jobs independently; budget release latency
accordingly. Merging the private `dev`-to-`main` pull request remains the
human decision that creates a release candidate.

GitHub suspends cron triggers in public repositories after 60 days without
repository activity. The deploy-drift probe remains the independent backstop
that alerts while a valid candidate is not shipping; a manual dispatch or
re-enable restores the automatic path.

For a least-privilege audit without a release, dispatch the same protected
workflow with `authority_probe=true` and any syntactically inert `release_tag`
input. After the production environment gate admits the run, the probe
verifies the App's exact one-repository/read-only boundary and the federated
identity's effective permissions. Candidate validation and the 17-build
approval step are skipped in this mode, so no build, release, or deployment is
started.

The controller also carries fail-closed WIF denial checks. Setting
`nonproduction_wif_probe=true` on `promote.yml` proves that the exact trusted
workflow cannot exchange outside the `production` environment. The separate
`wif-negative.yml` proves that a different workflow is denied even after the
production environment gate and that pull-request claims are denied. These jobs
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
dispatching actor recorded by GitHub. It must never publish private source, customer
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
