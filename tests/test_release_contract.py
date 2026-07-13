from pathlib import Path
import json
import os
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
AUTHORITIES = ROOT / "config" / "release-authorities.txt"
VERIFY_UPSTREAM = ROOT / "scripts" / "verify_upstream_candidate.sh"
APPROVE_BUILDS = ROOT / "scripts" / "approve_pending_release.sh"


def test_public_workflow_is_reviewer_gated_and_serialized():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    triggers = workflow.get("on") or workflow.get(True)
    assert triggers["workflow_dispatch"]["inputs"]["release_tag"]["required"] is True
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "customer-news-production",
        "cancel-in-progress": False,
    }

    job = workflow["jobs"]["promote"]
    assert job["environment"] == "production"
    assert job["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }


def test_public_main_has_a_read_only_pull_request_check():
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    commands = "\n".join(
        str(step.get("run", ""))
        for step in workflow["jobs"]["contract"]["steps"]
        if isinstance(step, dict)
    )

    assert "pull_request" in triggers
    assert triggers["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "pytest" in commands
    assert "bash -n scripts/*.sh" in commands


def test_public_workflow_uses_read_only_app_before_gcp_authentication():
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    steps = workflow["jobs"]["promote"]["steps"]
    uses = [str(step.get("uses", "")) for step in steps]

    app_index = next(i for i, use in enumerate(uses) if use.startswith("actions/create-github-app-token@"))
    auth_index = next(i for i, use in enumerate(uses) if use.startswith("google-github-actions/auth@"))
    assert app_index < auth_index
    assert "UPSTREAM_READ_APP_PRIVATE_KEY" in workflow_text
    assert "UPSTREAM_READ_APP_ID" in workflow_text
    assert "scripts/verify_upstream_candidate.sh" in workflow_text
    assert "scripts/approve_pending_release.sh" in workflow_text

    forbidden = (
        "gcloud builds submit",
        "gcloud builds triggers run",
        "gcloud builds triggers create",
        "gcloud builds triggers delete",
        "gcloud iam",
        "1password",
        " op ",
    )
    lowered = workflow_text.lower()
    for marker in forbidden:
        assert marker not in lowered


def test_upstream_verifier_binds_tag_sha_and_successful_private_run():
    script = VERIFY_UPSTREAM.read_text(encoding="utf-8")

    assert "UplixSEO/Uplix-Agents" in script
    assert "customer-news-release/" in script
    assert "actions/runs/${RUN_ID}" in script
    assert ".head_sha" in script
    assert ".head_branch" in script
    assert ".conclusion" in script
    assert ".event" in script
    assert "git/ref/tags/${RELEASE_TAG}" in script
    assert "git/tags/${TAG_OBJECT_SHA}" in script


def test_approval_script_accepts_only_exact_seventeen_pending_builds():
    authorities = [
        line.strip()
        for line in AUTHORITIES.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    script = APPROVE_BUILDS.read_text(encoding="utf-8")

    assert len(authorities) == 17
    assert len(set(authorities)) == 17
    assert "PENDING" in script
    assert "COMMIT_SHA" in script
    assert "TAG_NAME" in script
    assert "TRIGGER_NAME" in script
    assert "gcloud beta builds approve" in script
    assert "gcloud builds submit" not in script
    assert "gcloud builds triggers run" not in script


def _authority_names():
    return [
        line.strip()
        for line in AUTHORITIES.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _install_fake_gcloud(tmp_path: Path) -> Path:
    fake = tmp_path / "gcloud"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
builds = json.loads(os.environ["FAKE_BUILDS_JSON"])
if args[:2] == ["builds", "list"]:
    print(json.dumps(builds))
elif args[:2] == ["builds", "describe"]:
    build_id = args[2]
    print(json.dumps(next(row for row in builds if row["id"] == build_id)))
elif args[:3] == ["beta", "builds", "approve"]:
    with open(os.environ["FAKE_APPROVAL_LOG"], "a", encoding="utf-8") as handle:
        handle.write(args[3] + "\\n")
else:
    raise SystemExit(f"unexpected gcloud arguments: {args}")
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _pending_builds(extra_trigger=None):
    tag = "customer-news-release/123-" + "a" * 40
    rows = [
        {
            "id": f"build-{index}",
            "status": "PENDING",
            "substitutions": {
                "TAG_NAME": tag,
                "COMMIT_SHA": "a" * 40,
                "TRIGGER_NAME": trigger,
            },
        }
        for index, trigger in enumerate(_authority_names())
    ]
    if extra_trigger:
        rows.append(
            {
                "id": "build-extra",
                "status": "PENDING",
                "substitutions": {
                    "TAG_NAME": tag,
                    "COMMIT_SHA": "a" * 40,
                    "TRIGGER_NAME": extra_trigger,
                },
            }
        )
    return rows


def _run_approver(tmp_path: Path, builds):
    _install_fake_gcloud(tmp_path)
    approval_log = tmp_path / "approvals.log"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "AUTHORITIES_FILE": str(AUTHORITIES),
        "RELEASE_TAG": "customer-news-release/123-" + "a" * 40,
        "COMMIT_SHA": "a" * 40,
        "MAX_ATTEMPTS": "1",
        "SLEEP_SECONDS": "0",
        "FAKE_BUILDS_JSON": json.dumps(builds),
        "FAKE_APPROVAL_LOG": str(approval_log),
    }
    completed = subprocess.run(
        [str(APPROVE_BUILDS)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    return completed, approval_log


def test_approval_script_approves_every_fixed_pending_build_once(tmp_path):
    completed, approval_log = _run_approver(tmp_path, _pending_builds())

    assert completed.returncode == 0, completed.stderr
    approvals = approval_log.read_text(encoding="utf-8").splitlines()
    assert len(approvals) == 17
    assert len(set(approvals)) == 17
    assert all("/locations/europe-west1/builds/build-" in row for row in approvals)


def test_approval_script_rejects_unknown_release_build(tmp_path):
    completed, approval_log = _run_approver(
        tmp_path, _pending_builds(extra_trigger="unmanaged-prod")
    )

    assert completed.returncode != 0
    assert "unknown builds" in completed.stderr
    assert not approval_log.exists()
