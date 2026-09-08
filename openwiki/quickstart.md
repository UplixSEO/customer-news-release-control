---
type: operations
title: Customer News Release Control
description: The public least-privilege approval boundary for Customer News production promotion, rollback, proof, and release-ledger convergence.
tags: [release, customer-news, github-actions, cloud-build, approvals]
sources:
  - id: readme
    resource: repo://README.md
  - id: promote
    resource: repo://.github/workflows/promote.yml
  - id: auto
    resource: repo://.github/workflows/auto-promote.yml
  - id: ledger
    resource: repo://scripts/release_ledger.py
  - id: approve
    resource: repo://scripts/approve_pending_release.sh
verified:
  - by: openwiki/0.4.0
    at: 2026-09-01T07:53:32.757Z
generated: {by: "codex", at: "2026-09-01T07:53:32.757Z"}
---

# Customer News Release Control

This public repository is the production approval boundary for private Customer News source on
GitHub Free. It deliberately contains no product source, customer identifiers, build payloads,
runtime artifacts, or credentials.

## Release flow

The private repository's successful main guard creates an annotated release tag encoding a release
epoch, mode, and exact 40-character commit SHA. A manual dispatch or the scheduled poller passes
that exact tag into the protected `promote.yml` workflow.

The production environment gates access to a read-only GitHub App key and an OIDC-fed,
approver-only GCP identity. The workflow revalidates the private tag, guard run, merged-main
provenance, upstream head, ledger state, and cloud authority before approving an exact inventory of
17 already-pending Cloud Build triggers. It cannot create a build, invoke or edit a trigger, upload
source, impersonate the build service account, or cancel a build.

Approval is resumable: after interruption, only builds still in `PENDING` are approved.
`QUEUED`, `WORKING`, and `SUCCESS` builds are read back and left untouched; terminal failure or
an unknown trigger fails immediately.

## Ledger and ordering

GitHub Deployments in this repository are the append-only release ledger. The pure
`release_ledger.py` helper consumes normalized ledger and ancestry evidence and returns a
deterministic create, resume, already-succeeded, or superseded decision without making API calls.

A rollback must target a proven ancestor, carry a non-empty reason, and have compatibility approval.
A later successful release marks only ancestry-proven older entries inactive. A newer successful
epoch pins the runtime after rollback, preventing the scheduled poller from re-promoting a stale
private head.

## Automatic promotion

`auto-promote.yml` polls every 30 minutes, resolves the private main head and its newest exact
promote tag through the read-only App, consults the ledger helper, and dispatches only when no
promotion is already active. It has no OIDC permission and cannot acquire cloud authority.

The production environment uses a cancellable wait timer. Canceling one run delays that attempt;
a durable hold requires disabling the poller, rolling back, or landing a superseding candidate.
The private `dev` to `main` merge remains the human decision that creates the candidate.

## Verify locally

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
bash -n scripts/*.sh
```

Use `authority_probe=true` only for the protected, non-release least-privilege audit documented in
`README.md`. Promotion and rollback are production mutations and require the environment gate;
local tests and probes do not constitute deployment proof.
