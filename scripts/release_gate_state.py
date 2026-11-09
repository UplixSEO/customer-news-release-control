#!/usr/bin/env python3
"""Classify a Customer News production gate run or release-ledger record (read-only)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


WAIT_TIMER_MINUTES = 15
GRACE_SECONDS = 120
PRODUCTION_ENVIRONMENT = "production"
ACTIVE_DEPLOYMENT_STATES = {"waiting", "queued", "in_progress", "pending"}
LEDGER_RESUMABLE_STATES = {"pending", "in_progress", "failure"}
LEDGER_SUPERSEDED_ONLY_STATES = {"queued", "error"}
LEDGER_TERMINAL_STATES = {"success", "inactive"}
EXIT_CODES = {
    "gate-timer-pending": 0,
    "gate-admitted": 0,
    "gate-admitted-late": 3,
    "gate-stuck": 3,
    "run-terminal-residual": 0,
    "ledger-open-record": 0,
    "ledger-terminal": 0,
    "no-deployment": 2,
}


def _parse(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_status_at(statuses: list[dict[str, Any]], instant: datetime) -> dict[str, Any] | None:
    eligible = [row for row in statuses if _parse(row["created_at"]) <= instant]
    if not eligible:
        return None
    return max(eligible, key=lambda row: _parse(row["created_at"]))


def _no_deployment(error: str, **extra: Any) -> dict[str, Any]:
    return {"verdict": "no-deployment", "error": error, **extra}


def _matched_deployments(payload: dict[str, Any], job_created: datetime) -> list[dict[str, Any]]:
    matches = []
    for deployment in payload.get("deployments") or []:
        if deployment.get("environment") != PRODUCTION_ENVIRONMENT:
            continue
        delta = abs((_parse(deployment["created_at"]) - job_created).total_seconds())
        if delta <= 5:
            matches.append(deployment)
    return matches


def _classify_run(payload: dict[str, Any], instant: datetime, bound_seconds: float) -> dict[str, Any]:
    run = payload.get("run") or {}
    job = payload.get("job")
    if not job:
        return _no_deployment("no-production-deployment", note="no environment job resolved")

    matches = _matched_deployments(payload, _parse(job["created_at"]))
    if not matches:
        return _no_deployment("no-production-deployment")
    if len(matches) > 1:
        return _no_deployment(
            "ambiguous-production-deployment",
            candidates=sorted(int(deployment["id"]) for deployment in matches),
        )

    deployment = matches[0]
    status_row = _latest_status_at(deployment.get("statuses") or [], instant)
    deployment_status = status_row["state"] if status_row else None
    steps = job.get("steps") or []
    first_step = steps[0].get("started_at") if steps else None
    gate_started = _parse(deployment["created_at"])

    result: dict[str, Any] = {
        "mode": "run",
        "run_id": run.get("id"),
        "job_name": job.get("name"),
        "deployment_id": deployment.get("id"),
        "deployment_status": deployment_status,
        "gate_started_at": deployment["created_at"],
        "reference_instant": payload.get("reference_instant"),
        "bound_seconds": bound_seconds,
        "admission_seconds": None,
        "elapsed_seconds": None,
        "first_step_started_at": first_step,
    }

    if first_step:
        admission = (_parse(first_step) - gate_started).total_seconds()
        result["admission_seconds"] = admission
        result["verdict"] = "gate-admitted" if admission <= bound_seconds else "gate-admitted-late"
    else:
        elapsed = (instant - gate_started).total_seconds()
        result["elapsed_seconds"] = elapsed
        if run.get("status") == "completed":
            result["verdict"] = "run-terminal-residual"
        elif deployment_status == "error" or elapsed > bound_seconds:
            result["verdict"] = "gate-stuck"
        elif deployment_status in ACTIVE_DEPLOYMENT_STATES:
            result["verdict"] = "gate-timer-pending"
        else:
            result["verdict"] = "gate-stuck"

    result["pending_deployments"] = payload.get("pending_deployments") or []
    return result


def _classify_ledger(payload: dict[str, Any], instant: datetime) -> dict[str, Any]:
    deployment = payload.get("deployment")
    if not deployment:
        return _no_deployment("no-ledger-record")

    status_row = _latest_status_at(deployment.get("statuses") or [], instant)
    if status_row is None:
        return _no_deployment("no-ledger-status-at-reference-instant")

    state = status_row["state"]
    result: dict[str, Any] = {
        "mode": "deployment",
        "deployment_id": deployment.get("id"),
        "deployment_status": state,
        "status_at": status_row["created_at"],
        "reference_instant": payload.get("reference_instant"),
    }
    if state in LEDGER_RESUMABLE_STATES:
        result["verdict"] = "ledger-open-record"
        result["sub"] = "resumable"
    elif state in LEDGER_SUPERSEDED_ONLY_STATES:
        result["verdict"] = "ledger-open-record"
        result["sub"] = "superseded-only"
    elif state in LEDGER_TERMINAL_STATES:
        result["verdict"] = "ledger-terminal"
    else:
        return _no_deployment("no-ledger-record", deployment_status=state)
    return result


def classify(payload: dict[str, Any]) -> dict[str, Any]:
    instant = _parse(payload.get("reference_instant") or datetime.now(timezone.utc).isoformat())
    wait_minutes = payload.get("wait_timer_minutes", WAIT_TIMER_MINUTES)
    grace_seconds = payload.get("grace_seconds", GRACE_SECONDS)
    bound_seconds = float(wait_minutes) * 60 + float(grace_seconds)

    mode = payload.get("mode")
    if mode == "run":
        result = _classify_run(payload, instant, bound_seconds)
    elif mode == "deployment":
        result = _classify_ledger(payload, instant)
    else:
        raise ValueError("mode must be run or deployment")
    if payload.get("api_errors"):
        result["api_errors"] = payload["api_errors"]
    return result


def main() -> int:
    result = classify(json.load(sys.stdin))
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    sys.stdout.write(f"{result['verdict']}\n")
    return EXIT_CODES[result["verdict"]]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
