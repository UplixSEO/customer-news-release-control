#!/usr/bin/env python3
"""Evaluate an exact fixed Cloud Build release batch without mutating builds."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any


TERMINAL_STATUSES = {
    "SUCCESS",
    "FAILURE",
    "INTERNAL_ERROR",
    "TIMEOUT",
    "CANCELLED",
    "EXPIRED",
}


class BuildStateError(RuntimeError):
    pass


def evaluate_batch(
    builds: list[dict[str, Any]],
    *,
    expected_authorities: list[str],
    phase: str,
) -> dict[str, Any]:
    if phase not in {"pending", "terminal", "success"}:
        raise BuildStateError("phase must be pending, terminal, or success")
    if len(expected_authorities) != 17 or len(set(expected_authorities)) != 17:
        raise BuildStateError("authority inventory must contain exactly 17 unique names")

    expected = set(expected_authorities)
    triggers = [str(row.get("trigger", "")) for row in builds]
    counts = Counter(triggers)
    observed = set(triggers)
    missing = sorted(expected - observed)
    unknown = sorted(observed - expected)
    duplicate = sorted(trigger for trigger, count in counts.items() if count > 1)
    nonterminal: list[str] = []
    failed: list[str] = []
    wrong_state: list[str] = []

    for row in builds:
        trigger = str(row.get("trigger", ""))
        status = str(row.get("status", ""))
        if trigger not in expected:
            continue
        if phase == "pending" and status != "PENDING":
            wrong_state.append(trigger)
        elif phase == "terminal" and status not in TERMINAL_STATUSES:
            nonterminal.append(trigger)
        elif phase == "success":
            if status not in TERMINAL_STATUSES:
                nonterminal.append(trigger)
            elif status != "SUCCESS":
                failed.append(trigger)

    result = {
        "phase": phase,
        "expected": len(expected_authorities),
        "observed": len(builds),
        "missing": missing,
        "duplicate": duplicate,
        "unknown": unknown,
        "nonterminal": sorted(set(nonterminal)),
        "failed": sorted(set(failed)),
        "wrong_state": sorted(set(wrong_state)),
    }
    result["ready"] = not any(
        result[key]
        for key in (
            "missing",
            "duplicate",
            "unknown",
            "nonterminal",
            "failed",
            "wrong_state",
        )
    ) and len(builds) == len(expected_authorities)
    result["terminal_failure"] = (
        phase == "success"
        and not any(result[key] for key in ("missing", "duplicate", "unknown", "nonterminal"))
        and bool(result["failed"])
        and len(builds) == len(expected_authorities)
    )
    return result


def _load_authorities(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=["pending", "terminal", "success"])
    parser.add_argument("--authorities", default="config/release-authorities.txt")
    args = parser.parse_args(argv)
    builds = json.load(sys.stdin)
    result = evaluate_batch(
        builds,
        expected_authorities=_load_authorities(Path(args.authorities)),
        phase=args.phase,
    )
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    if result["ready"]:
        return 0
    if result["terminal_failure"]:
        return 2
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildStateError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
