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
successful guard run plus the unique merged PR into `main` for that SHA
(usually `dev`; a same-tree reconcile branch is accepted because GitHub cannot
open a second empty `dev`→`main` PR after that merge),
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
`promote`, and `prove` jobs independently; two `waiting` deployments can
coexist and neither queues the other, while two `promote.yml` runs still
serialize on `customer-news-production` before the gate. Budget release latency
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
approval step are skipped in this mode: two production gate records are created
(the `promote` and `proof-audit` gates) but no build, release or ledger
deployment is started.

The controller also carries fail-closed WIF denial checks. Setting
`nonproduction_wif_probe=true` on `promote.yml` proves that the exact trusted
workflow cannot exchange outside the `production` environment. The separate
`wif-negative.yml` proves that a different workflow is denied even after the
production environment gate and that pull-request claims are denied. These jobs
only invoke the native OIDC authentication action and assert its failure; they
contain no GitHub, Cloud Build, IAM, or storage mutation commands.

Uplix credentials are stored only in Uplix-owned GitHub/GCP control planes.
Léonard's personal 1Password stack is not a dependency or recovery store.

## When a production run stays waiting

The `production` environment has exactly two protection rules: `branch_policy`
(the ref must be `main`) and `wait_timer: 15`. There is no `required_reviewers`
rule, so **nobody approves** a normal release. The timer is the release
mechanism, and GitHub correctly reports `reviewers: []` and
`current_user_can_approve: false` in `pending_deployments` while a job waits
with zero steps. A live `waiting` deployment is the normal shape of the timer,
not a deadlock; cancelling the run inside the window is the only manual
intervention.

Each release creates two different records:

* the **production gate record**, created by GitHub when a job with
  `environment: production` starts (`production_environment: false`, empty
  payload); it stays `waiting` until the timer elapses, then moves through
  `queued` and `in_progress`;
* the **release ledger record**, created by `scripts/github_deployment_ledger.sh`
  in the `customer-news-runtime` environment (`production_environment: true`,
  `schema=customer_news_release_v1`); it carries the resume state, stays
  `in_progress` across failed attempts by design, and becomes `inactive` with
  `superseded_by=<sha>` only after a descendant succeeds.

An `authority_probe` run creates two production gate records (the `promote` and
`proof-audit` gates) and no build, release or ledger deployment.

Classify any run or ledger record with the read-only helper
(already-authenticated `gh`; no writes, and failed API sections are reported in
`api_errors`):

```bash
scripts/diagnose_release_gate.sh <run-id>
scripts/diagnose_release_gate.sh --deployment <deployment-id> [--reference-instant <iso8601>]
scripts/diagnose_release_gate.sh --fixture <payload.json>
```

The last line is exactly one verdict; the exit code is 0 (normal), 3 (stuck or
late) or 2 (record not resolvable):

| Verdict | Meaning | Action |
| -- | -- | -- |
| `gate-timer-pending` | live run, no first step, gate elapsed ≤ 17 min, deployment `waiting`/`queued`/`in_progress`/`pending` | none; the timer will admit it |
| `gate-admitted` | first step exists, admission ≤ 17 min | none |
| `gate-admitted-late` | first step exists, admission > 17 min | record the delay; the run proceeds |
| `gate-stuck` | live run, no first step, gate elapsed > 17 min or deployment `error`; the record's status is printed | capture the evidence below, then use the fallback if the release must ship |
| `run-terminal-residual` | run terminal, deployment `waiting`/`error`, no step | a dead record, not a hold; nothing to release |
| `ledger-open-record` | ledger status at the reference instant is `pending`/`queued`/`in_progress`/`failure`/`error` | `resumable` (`pending`/`in_progress`/`failure`) → resume the same record; `superseded-only` (`queued`/`error`) → the next attempt creates a new one |
| `ledger-terminal` | ledger status is `success` or `inactive` | none |
| `no-deployment` | no matching production gate (`no-production-deployment`), two gates in the window (`ambiguous-production-deployment`, candidate ids listed), or no ledger record (`no-ledger-record`) | pass `--job`, or use the ledger path |

For a genuinely stuck release, capture the run URL, the deployment id and its
status timeline, the `pending_deployments` payload (`wait_timer`,
`wait_timer_started_at`, `reviewers`), the build ids and the `approval.result`.
Then use the documented fallback: `scripts/approve_pending_release.sh`, or for
an already-created `PENDING` build

```bash
gcloud alpha builds approve projects/customer-news-475010/locations/europe-west1/builds/<build_id> \
  --comment="Protected Customer News release <sha>" --quiet
```

run as `cn-release-approver@customer-news-475010.iam.gserviceaccount.com` (it
holds `roles/cloudbuild.builds.approver`) or the owner identity with the same
role. The 2026-11-07 direct approval used the owner identity; it is recorded
here as precedent, not as the default.

To hold a release durably, use `gh workflow disable auto-promote.yml`, roll
back (the new epoch pins the runtime), or land a superseding candidate. Two
`waiting` deployments can coexist and neither queues the other, while two
`promote.yml` runs still serialize on the `customer-news-production`
concurrency group before the gate.

## Local verification

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
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
`already_succeeded`, or `superseded`. Its `auto-promote` operation feeds the
poller the same way — from the upstream head, its candidate tag, and the
ledger snapshot it returns `dispatch` plus a reason (`no_candidate`,
`already_accepted`, `accepted_epoch_supersedes_candidate`, or
`candidate_ready`). An interrupted release resumes the same
Deployment ID; an older candidate becomes inactive only after a proven
descendant succeeds. Rollback creates a new release-tag epoch and is accepted
only for a compatibility-approved ancestor with a non-empty reason.

Each release also creates one production gate record per `environment: production`
job (see §When a production run stays waiting). They are not ledger entries: the
production gate record has an empty payload and `production_environment: false`,
while the ledger record carries `schema=customer_news_release_v1` and
`production_environment: true`. An `authority_probe` run creates two production
gate records (the `promote` and `proof-audit` gates) and no ledger deployment;
only reads are performed, so no build, release or ledger deployment is started.
