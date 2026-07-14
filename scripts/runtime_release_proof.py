#!/usr/bin/env python3
"""Read-only dynamic proof for one exact Customer News production release."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SUPPORTED_KINDS = {
    "bigquery_sql",
    "cloud_build_trigger",
    "cloud_function",
    "cloud_run_job",
    "cloud_run_service",
}


class ProofError(RuntimeError):
    pass


def run_json(args: list[str]) -> Any:
    completed = subprocess.run(args, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        if "NOT_FOUND" in detail or "not found" in detail.lower():
            raise FileNotFoundError(detail)
        raise ProofError(f"read command failed: {args[0]} {args[1] if len(args) > 1 else ''}")
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProofError(f"invalid JSON from {args[0]}") from exc


def first_image(value: Any) -> str | None:
    if isinstance(value, dict):
        containers = value.get("containers")
        if isinstance(containers, list) and containers:
            image = containers[0].get("image") if isinstance(containers[0], dict) else None
            if image:
                return str(image)
        for child in value.values():
            if image := first_image(child):
                return image
    elif isinstance(value, list):
        for child in value:
            if image := first_image(child):
                return image
    return None


def labels(value: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for candidate in (
        value.get("labels"),
        value.get("metadata", {}).get("labels"),
        value.get("spec", {}).get("template", {}).get("metadata", {}).get("labels"),
    ):
        if isinstance(candidate, dict):
            result.update({str(key): str(item) for key, item in candidate.items()})
    return result


def describe_target(
    target: dict[str, Any], project: str, region: str
) -> dict[str, Any]:
    kind = target["kind"]
    name = target["name"]
    common = [f"--project={project}", f"--region={region}", "--format=json"]
    if kind == "cloud_run_service":
        return run_json(["gcloud", "run", "services", "describe", name, *common])
    if kind == "cloud_run_job":
        return run_json(["gcloud", "run", "jobs", "describe", name, *common])
    if kind == "cloud_function":
        return run_json(
            ["gcloud", "functions", "describe", name, *common, "--gen2"]
        )
    if kind == "cloud_build_trigger":
        return run_json(
            ["gcloud", "builds", "triggers", "describe", name, *common]
        )
    raise ProofError(f"unsupported describe kind {kind}")


def trigger_config(value: dict[str, Any]) -> str | None:
    return value.get("filename") or value.get("gitFileSource", {}).get("path")


def evaluate_target(
    target: dict[str, Any],
    *,
    expected_sha: str,
    accepted_by_trigger: dict[str, dict[str, Any]],
    bq_markers: dict[str, dict[str, Any]],
    project: str,
    region: str,
) -> dict[str, Any]:
    target_id = str(target.get("id", ""))
    kind = str(target.get("kind", ""))
    row: dict[str, Any] = {"id": target_id, "kind": kind, "outcome": "unknown"}
    if kind not in SUPPORTED_KINDS:
        return row

    authority = target.get("deploy_authority") or {}
    authority_type = authority.get("type") if isinstance(authority, dict) else None
    authority_trigger = authority.get("trigger") if isinstance(authority, dict) else None
    accepted = accepted_by_trigger.get(str(authority_trigger)) if authority_trigger else None
    if authority_type in {"mandatory_trigger", "repin_trigger"}:
        if accepted is None:
            row["outcome"] = "missing"
            return row
        row["accepted_build_id"] = accepted.get("id")
        if accepted.get("status") != "SUCCESS":
            row["outcome"] = "mismatched"
            return row

    try:
        if kind == "bigquery_sql":
            marker = bq_markers.get(target_id)
            if marker is None:
                row["outcome"] = "missing"
            elif marker.get("release_sha") != expected_sha:
                row["outcome"] = "mismatched"
            else:
                row["outcome"] = "matched"
            return row

        observed = describe_target(target, project, region)
        if kind in {"cloud_run_service", "cloud_run_job"}:
            image = first_image(observed)
            observed_labels = labels(observed)
            row["observed_image"] = image
            row["outcome"] = (
                "matched"
                if observed_labels.get("release_sha") == expected_sha
                or bool(image and image.endswith(f":{expected_sha}"))
                else "mismatched"
            )
        elif kind == "cloud_function":
            observed_labels = labels(observed)
            row["outcome"] = (
                "matched"
                if observed.get("state") == "ACTIVE"
                and observed_labels.get("release_sha") == expected_sha
                and (
                    accepted is None
                    or observed_labels.get("release_build_id") == accepted.get("id")
                )
                else "mismatched"
            )
        else:
            expected_config = target.get("trigger_config_path") or (
                f"customer-news/{target['config']}" if target.get("config") else None
            )
            exact = trigger_config(observed) == expected_config
            if target.get("service_account"):
                exact = exact and observed.get("serviceAccount") == target["service_account"]
            if target.get("included_files") is not None:
                exact = exact and sorted(observed.get("includedFiles", [])) == sorted(
                    target.get("included_files", [])
                )
            row["outcome"] = "matched" if exact else "mismatched"
    except FileNotFoundError:
        row["outcome"] = "missing"
    except ProofError:
        row["outcome"] = "unknown"
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-json", required=True)
    parser.add_argument("--accepted-builds-json", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--project", default="customer-news-475010")
    parser.add_argument("--region", default="europe-west1")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if not SHA_RE.fullmatch(args.expected_sha):
        raise ProofError("expected SHA must be exact 40-hex")

    inventory = json.loads(Path(args.inventory_json).read_text(encoding="utf-8"))
    builds = json.loads(Path(args.accepted_builds_json).read_text(encoding="utf-8"))
    targets = inventory.get("targets", [])
    prod = [
        row
        for row in targets
        if row.get("deployment") == "automatic" and row.get("environment") == "prod"
    ]
    dev = sorted(
        str(row.get("id"))
        for row in targets
        if row.get("deployment") == "automatic" and row.get("environment") == "dev"
    )
    accepted_by_trigger = {str(row.get("trigger")): row for row in builds}

    marker_rows = run_json(
        [
            "bq",
            "--headless=true",
            "--quiet=true",
            "--format=json",
            "head",
            "--max_rows=100",
            f"{args.project}:customer_news_core.release_proof",
        ]
    )
    bq_markers = {
        str(row.get("target_id")): row
        for row in marker_rows
        if isinstance(row, dict)
    }
    results = [
        evaluate_target(
            target,
            expected_sha=args.expected_sha,
            accepted_by_trigger=accepted_by_trigger,
            bq_markers=bq_markers,
            project=args.project,
            region=args.region,
        )
        for target in prod
    ]
    counts = {key: 0 for key in ("missing", "mismatched", "unknown", "skipped")}
    for row in results:
        if row["outcome"] in counts:
            counts[row["outcome"]] += 1
    payload = {
        "schema": "customer_news_runtime_proof_v1",
        "sha": args.expected_sha,
        "aggregate": {"expected": len(prod), **counts},
        "dev_targets": dev,
        "targets": results,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if not any(counts.values()) and len(results) == len(prod) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProofError, json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
