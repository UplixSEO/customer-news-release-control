#!/usr/bin/env python3
"""Pure release-ledger decisions for the native GitHub Deployment workflow."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Mapping


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(
    r"^customer-news-release/(?P<epoch>[1-9][0-9]*)-"
    r"(?P<mode>promote|rollback)-(?P<sha>[0-9a-f]{40})$"
)
RESUMABLE_STATES = {"pending", "in_progress", "failure"}


class LedgerError(RuntimeError):
    pass


def _validate_candidate(
    candidate_sha: str, current_head_sha: str, release_tag: str, mode: str
) -> None:
    if not SHA_RE.fullmatch(candidate_sha) or not SHA_RE.fullmatch(current_head_sha):
        raise LedgerError("candidate and current-head SHAs must be exact 40-hex values")
    match = TAG_RE.fullmatch(release_tag)
    if match is None or match.group("sha") != candidate_sha or match.group("mode") != mode:
        raise LedgerError("release tag must encode the exact candidate SHA and mode")


def _is_ancestor(
    ancestor: str,
    descendant: str,
    ancestry: Mapping[tuple[str, str], bool],
) -> bool:
    if ancestor == descendant:
        return True
    return ancestry.get((ancestor, descendant), False)


def _exact_entry(
    entries: list[dict[str, Any]],
    *,
    sha: str,
    release_tag: str,
    mode: str,
) -> dict[str, Any] | None:
    matches = [
        entry
        for entry in entries
        if entry.get("sha") == sha
        and entry.get("release_tag") == release_tag
        and entry.get("mode", "promote") == mode
    ]
    if len(matches) > 1:
        raise LedgerError("release ledger contains duplicate exact entries")
    return matches[0] if matches else None


def decide_release(
    *,
    candidate_sha: str,
    current_head_sha: str,
    release_tag: str,
    mode: str,
    entries: list[dict[str, Any]],
    ancestry: Mapping[tuple[str, str], bool],
    compatibility_approved: bool = False,
    rollback_reason: str = "",
) -> dict[str, Any]:
    """Return a deterministic create/resume/supersede decision.

    Network reads and GitHub Deployment writes remain in the workflow. This
    function only consumes their normalized state and never performs I/O.
    """

    if mode not in {"promote", "rollback"}:
        raise LedgerError("mode must be promote or rollback")
    _validate_candidate(candidate_sha, current_head_sha, release_tag, mode)

    exact = _exact_entry(
        entries,
        sha=candidate_sha,
        release_tag=release_tag,
        mode=mode,
    )
    if exact is not None:
        state = str(exact.get("state", ""))
        deployment_id = exact.get("deployment_id")
        if state == "success":
            return {
                "action": "already_succeeded",
                "deployment_id": deployment_id,
                "mode": mode,
                "sha": candidate_sha,
                "superseded_by": None,
            }
        if state in RESUMABLE_STATES:
            return {
                "action": "resume",
                "deployment_id": deployment_id,
                "mode": mode,
                "sha": candidate_sha,
                "superseded_by": None,
            }
        raise LedgerError(f"exact ledger entry has non-resumable state {state!r}")

    if mode == "rollback":
        if not compatibility_approved or not rollback_reason.strip():
            raise LedgerError("rollback requires a compatibility-approved reason")
        if candidate_sha == current_head_sha:
            raise LedgerError("rollback target must be an ancestor of current head")
        if not _is_ancestor(candidate_sha, current_head_sha, ancestry):
            raise LedgerError("rollback target is not an ancestor of current head")
    elif candidate_sha != current_head_sha:
        if _is_ancestor(candidate_sha, current_head_sha, ancestry):
            return {
                "action": "superseded",
                "deployment_id": None,
                "mode": mode,
                "sha": candidate_sha,
                "superseded_by": current_head_sha,
            }
        raise LedgerError("promotion candidate is not an ancestor of current head")

    successful_entries = [entry for entry in entries if entry.get("state") == "success"]
    for entry in successful_entries:
        prior_sha = str(entry.get("sha", ""))
        if prior_sha == candidate_sha:
            continue
        if mode == "promote" and not _is_ancestor(prior_sha, candidate_sha, ancestry):
            raise LedgerError("current candidate does not descend from the successful ledger head")

    return {
        "action": "create",
        "deployment_id": None,
        "mode": mode,
        "sha": candidate_sha,
        "superseded_by": None,
    }


def supersession_targets(
    *,
    successful_sha: str,
    entries: list[dict[str, Any]],
    ancestry: Mapping[tuple[str, str], bool],
    release_succeeded: bool,
) -> list[dict[str, Any]]:
    if not release_succeeded:
        return []
    if not SHA_RE.fullmatch(successful_sha):
        raise LedgerError("successful SHA must be exact 40-hex")

    targets: list[dict[str, Any]] = []
    for entry in entries:
        prior_sha = str(entry.get("sha", ""))
        if entry.get("state") != "success" or prior_sha == successful_sha:
            continue
        if _is_ancestor(prior_sha, successful_sha, ancestry):
            targets.append(
                {
                    "deployment_id": entry.get("deployment_id"),
                    "superseded_by": successful_sha,
                }
            )
    return sorted(targets, key=lambda item: int(item["deployment_id"]))


def _ancestry_from_json(raw: Mapping[str, Any]) -> dict[tuple[str, str], bool]:
    parsed: dict[tuple[str, str], bool] = {}
    for pair, value in raw.items():
        parts = pair.split("..", 1)
        if len(parts) != 2:
            raise LedgerError(f"invalid ancestry key {pair!r}")
        parsed[(parts[0], parts[1])] = bool(value)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["decide", "supersede"])
    args = parser.parse_args(argv)
    payload = json.load(sys.stdin)
    ancestry = _ancestry_from_json(payload.pop("ancestry", {}))
    if args.operation == "decide":
        result = decide_release(ancestry=ancestry, **payload)
    else:
        result = supersession_targets(ancestry=ancestry, **payload)
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LedgerError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
