from pathlib import Path
import json
import os
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
NEGATIVE_WIF_WORKFLOW = ROOT / ".github" / "workflows" / "wif-negative.yml"
AUTO_PROMOTE_WORKFLOW = ROOT / ".github" / "workflows" / "auto-promote.yml"
AUTHORITIES = ROOT / "config" / "release-authorities.txt"
VERIFY_UPSTREAM = ROOT / "scripts" / "verify_upstream_candidate.sh"
APPROVE_BUILDS = ROOT / "scripts" / "approve_pending_release.sh"
PROBE_UPSTREAM_APP = ROOT / "scripts" / "probe_upstream_app.sh"
PROBE_GCP_AUTHORITY = ROOT / "scripts" / "probe_gcp_authority.sh"
DEPLOYMENT_LEDGER = ROOT / "scripts" / "github_deployment_ledger.sh"
RUNTIME_PROOF = ROOT / "scripts" / "runtime_release_proof.py"
WAIT_RELEASE_BATCH = ROOT / "scripts" / "wait_release_batch.sh"


def test_every_github_action_is_pinned_to_an_immutable_commit():
    for workflow_path in (
        WORKFLOW,
        CI_WORKFLOW,
        NEGATIVE_WIF_WORKFLOW,
        AUTO_PROMOTE_WORKFLOW,
    ):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                action = str(step.get("uses", ""))
                if action:
                    assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), (
                        f"{workflow_path}: mutable action reference {action}"
                    )


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
        "deployments": "write",
        "id-token": "write",
    }


def test_auto_promote_poller_is_scheduled_ledger_gated_and_holds_no_cloud_authority():
    text = AUTO_PROMOTE_WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)

    triggers = workflow.get("on") or workflow.get(True)
    assert triggers["schedule"] == [{"cron": "*/30 * * * *"}]
    assert "workflow_dispatch" in triggers

    assert workflow["permissions"] == {
        "contents": "read",
        "actions": "write",
        "deployments": "read",
    }
    assert workflow["concurrency"] == {
        "group": "customer-news-auto-promote",
        "cancel-in-progress": False,
    }

    assert list(workflow["jobs"]) == ["detect-and-dispatch"]
    job = workflow["jobs"]["detect-and-dispatch"]
    # The read-only upstream App key is a production-environment secret, so
    # the poller binds the same gate as every release job. It still must not
    # request id-token authority, so it can never exchange WIF credentials.
    assert job["environment"] == "production"
    assert "permissions" not in job

    app_steps = [
        step
        for step in job["steps"]
        if "create-github-app-token" in str(step.get("uses", ""))
    ]
    assert len(app_steps) == 1
    app_with = app_steps[0]["with"]
    assert app_with["owner"] == "UplixSEO"
    assert app_with["repositories"] == "Uplix-Agents"
    assert app_with["permission-contents"] == "read"
    assert app_with["permission-metadata"] == "read"

    commands = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "github_deployment_ledger.sh snapshot" in commands
    assert "release_ledger.py auto-promote" in commands
    assert "workflows/promote.yml/runs" in commands
    assert "gh workflow run promote.yml" in commands
    assert "--ref main" in commands
    assert "-f mode=promote" in commands
    assert '-f release_tag="${RELEASE_TAG}"' in commands

    steps_by_name = {str(step.get("name", "")): step for step in job["steps"]}
    hold = steps_by_name["Hold while a promote run is already active"]
    dispatch = steps_by_name["Dispatch promote for the exact candidate"]
    assert hold["if"] == "steps.decision.outputs.dispatch == 'true'"
    assert dispatch["if"] == (
        "steps.decision.outputs.dispatch == 'true' "
        "&& steps.inflight.outputs.proceed == 'true'"
    )
    assert dispatch["env"]["RELEASE_TAG"] == "${{ steps.candidate.outputs.tag }}"

    # The unattended poller only selects a tag; it must never carry rollback,
    # probe, or cloud-identity authority of its own.
    for forbidden in (
        "rollback",
        "compatibility_approved",
        "authority_probe",
        "id-token",
        "google-github-actions",
    ):
        assert forbidden not in text


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
    assert "permission-pull-requests: read" in workflow_text
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


def test_rollback_candidate_accepts_dispatch_from_current_main_for_ancestor(tmp_path):
    ancestor = "a" * 40
    current_main = "b" * 40
    tag_object = "c" * 40
    run_id = "30000000001"
    release_tag = f"customer-news-release/{run_id}-rollback-{ancestor}"
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        f"""#!/usr/bin/env python3
import json
import sys

path = next(arg for arg in sys.argv if arg.startswith("repos/"))
if "/git/ref/tags/" in path:
    print(json.dumps({{"object": {{"type": "tag", "sha": "{tag_object}"}}}}))
elif f"/git/tags/{{'{tag_object}'}}" in path:
    print(json.dumps({{"object": {{"type": "commit", "sha": "{ancestor}"}}}}))
elif f"/actions/runs/{{'{run_id}'}}" in path:
    print(json.dumps({{
        "head_branch": "main",
        "head_sha": "{current_main}",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/customer-news-cloudbuild-guard.yml",
        "head_repository": {{"full_name": "UplixSEO/Uplix-Agents", "private": True}}
    }}))
elif f"/commits/{{'{ancestor}'}}/pulls" in path:
    print(json.dumps([{{
        "number": 458,
        "state": "closed",
        "merged_at": "2026-07-14T00:00:00Z",
        "merge_commit_sha": "{ancestor}",
        "base": {{"ref": "main", "repo": {{"full_name": "UplixSEO/Uplix-Agents"}}}},
        "head": {{"ref": "dev", "repo": {{"full_name": "UplixSEO/Uplix-Agents"}}}}
    }}]))
else:
    raise SystemExit(f"unexpected gh path: {{path}}")
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    completed = subprocess.run(
        [str(VERIFY_UPSTREAM)],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GH_TOKEN": "test-token",
            "RELEASE_TAG": release_tag,
        },
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"sha={ancestor}" in completed.stdout
    assert "mode=rollback" in completed.stdout


def test_public_workflow_has_a_non_mutating_authority_probe_mode():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    inputs = triggers["workflow_dispatch"]["inputs"]
    steps = workflow["jobs"]["promote"]["steps"]
    steps_by_name = {step["name"]: step for step in steps}

    assert inputs["authority_probe"] == {
        "description": "Verify identity boundaries without approving a release",
        "required": False,
        "default": False,
        "type": "boolean",
    }
    assert inputs["nonproduction_wif_probe"] == {
        "description": "Prove the exact workflow is denied outside production",
        "required": False,
        "default": False,
        "type": "boolean",
    }
    assert workflow["jobs"]["promote"]["if"] == (
        "!inputs.nonproduction_wif_probe"
    )
    nonproduction = workflow["jobs"]["negative-nonproduction"]
    assert nonproduction["if"] == "inputs.nonproduction_wif_probe"
    assert nonproduction["environment"] == "authority-probe-nonproduction"
    assert nonproduction["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert steps_by_name["Probe read-only private-upstream boundary"]["if"] == (
        "inputs.authority_probe"
    )
    assert steps_by_name["Probe read-only private-upstream boundary"]["run"] == (
        "scripts/probe_upstream_app.sh"
    )
    assert steps_by_name["Probe approver-only GCP boundary"]["if"] == (
        "inputs.authority_probe"
    )
    assert steps_by_name["Probe approver-only GCP boundary"]["run"] == (
        "scripts/probe_gcp_authority.sh"
    )
    assert steps_by_name["Validate exact private candidate"]["if"] == (
        "!inputs.authority_probe"
    )
    assert steps_by_name["Approve exact fixed batch"]["if"] == (
        "steps.deployment.outputs.proceed == 'true'"
    )
    proof_audit = workflow["jobs"]["proof-audit"]
    assert proof_audit["if"] == "inputs.authority_probe"
    assert proof_audit["environment"] == "production"
    assert proof_audit["permissions"] == {"contents": "read", "id-token": "write"}
    proof_steps = {step["name"]: step for step in proof_audit["steps"]}
    assert proof_steps["Authenticate as read-only runtime proof"]["with"] == {
        "workload_identity_provider": "${{ vars.GCP_PROOF_WIF_PROVIDER }}",
        "service_account": "${{ vars.GCP_PROOF_SERVICE_ACCOUNT }}",
    }
    assert proof_steps["Probe proof-only GCP boundary"]["run"] == (
        "scripts/probe_gcp_proof_authority.sh"
    )


def test_authority_probe_scripts_are_read_only_and_fail_closed():
    app_probe = PROBE_UPSTREAM_APP.read_text(encoding="utf-8")
    gcp_probe = PROBE_GCP_AUTHORITY.read_text(encoding="utf-8")

    assert "installation/repositories" in app_probe
    assert "apps/uplix-customer-news-release-proof" in app_probe
    assert "4290359" in app_probe
    assert "UplixSEO/Uplix-Agents" in app_probe
    assert "UplixSEO/uplixOS" in app_probe
    assert '"actions":"read"' in app_probe
    assert '"contents":"read"' in app_probe
    assert '"metadata":"read"' in app_probe
    assert '"pull_requests":"read"' in app_probe

    required_gcp_markers = (
        "cloudbuild.builds.approve",
        "cloudbuild.builds.create",
        "cloudbuild.builds.update",
        "iam.serviceAccounts.actAs",
        "storage.objects.create",
        "resourcemanager.projects.setIamPolicy",
    )
    assert all(marker in gcp_probe for marker in required_gcp_markers)
    assert "cancellation is gated by cloudbuild.builds.update" in gcp_probe
    assert "triggers.run is gated by cloudbuild.builds.create" in gcp_probe
    assert "triggers.create/patch/delete/run are gated by cloudbuild.builds.create" in gcp_probe
    assert "PROBE_TRIGGER_ID" not in gcp_probe
    assert "gcloud builds triggers describe" not in gcp_probe

    forbidden = (
        "gcloud builds submit",
        "gcloud builds triggers run",
        "gcloud builds cancel",
        "gcloud beta builds approve",
        "gcloud alpha builds approve",
        "gcloud projects add-iam-policy-binding",
        "gcloud storage cp",
    )
    combined = f"{app_probe}\n{gcp_probe}".lower()
    assert all(marker not in combined for marker in forbidden)


def test_negative_wif_workflow_proves_pr_and_other_workflow_denials():
    workflow_text = NEGATIVE_WIF_WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    triggers = workflow.get("on") or workflow.get(True)

    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "read"}

    other = workflow["jobs"]["other-workflow-production"]
    assert other["environment"] == "production"
    assert other["permissions"] == {"contents": "read", "id-token": "write"}
    pull_request = workflow["jobs"]["pull-request"]
    assert pull_request["permissions"] == {"contents": "read", "id-token": "write"}

    for job in (other, pull_request):
        auth = next(step for step in job["steps"] if step.get("id") == "denied-auth")
        assert auth["continue-on-error"] is True
        assertion = next(step for step in job["steps"] if step.get("name") == "Assert exchange denied")
        assert "steps.denied-auth.outcome" in assertion["run"]
        assert "failure" in assertion["run"]

    forbidden = (
        "gcloud builds",
        "gcloud iam",
        "gcloud storage",
        "gh api",
    )
    lowered = workflow_text.lower()
    assert all(marker not in lowered for marker in forbidden)


def test_production_workflow_uses_native_deployment_ledger_and_current_head_reread():
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    triggers = workflow.get("on") or workflow.get(True)
    inputs = triggers["workflow_dispatch"]["inputs"]
    steps = workflow["jobs"]["promote"]["steps"]
    names = [step["name"] for step in steps]

    assert inputs["mode"]["type"] == "choice"
    assert inputs["mode"]["options"] == ["promote", "rollback"]
    assert inputs["compatibility_approved"]["type"] == "boolean"
    assert inputs["rollback_reason"]["type"] == "string"
    assert "Read current private main" in names
    assert "Read Deployment ledger" in names
    assert "Decide create, resume, or supersede" in names
    assert "Create or resume GitHub Deployment" in names
    assert "Re-read current private main before approval" in names
    assert "Wait for prior release mutations to quiesce" in names
    assert "Wait for exact release batch success" in names
    assert "Mark GitHub Deployment successful" not in names
    assert "Mark GitHub Deployment successful" in [
        step["name"] for step in workflow["jobs"]["prove"]["steps"]
    ]
    assert "Supersede ancestry-proven older Deployments" in [
        step["name"] for step in workflow["jobs"]["prove"]["steps"]
    ]
    assert names.index("Re-read current private main before approval") < names.index(
        "Approve exact fixed batch"
    )
    assert "scripts/release_ledger.py" in workflow_text
    assert "scripts/github_deployment_ledger.sh" in workflow_text
    assert "scripts/release_ledger.py supersede" in workflow_text
    assert "scripts/wait_release_batch.sh" in workflow_text
    assert 'scripts/wait_release_batch.sh quiescent "${tag}" "${sha}"' in workflow_text
    assert "cancel-in-progress: false" in workflow_text


def test_deployment_ledger_client_is_a_thin_native_github_api_surface():
    script = DEPLOYMENT_LEDGER.read_text(encoding="utf-8")

    assert "gh api" in script
    assert "customer_news_release_v1" in script
    assert "customer-news-runtime" in script
    assert "deployments" in script
    assert "statuses" in script
    assert "superseded_by" in script
    assert "curl " not in script
    assert "http://" not in script
    assert "https://" not in script


def test_deployment_ledger_snapshot_parses_supersession_with_runner_jq(tmp_path):
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import sys

path = next(arg for arg in sys.argv if arg.startswith("repos/"))
if path.endswith("/123/statuses"):
    print(json.dumps({"state": "in_progress", "description": "Customer News release in_progress"}))
elif path.endswith("/statuses"):
    print(json.dumps({"state": "inactive", "description": "superseded_by=" + "b" * 40}))
else:
    print(json.dumps([
        {
            "id": 123,
            "payload": {
                "schema": "customer_news_release_v1",
                "upstream_sha": "a" * 40,
                "release_tag": "customer-news-release/123-promote-" + "a" * 40,
                "mode": "promote"
            }
        },
        {
            "id": 124,
            "payload": {
                "schema": "customer_news_release_v1",
                "upstream_sha": "b" * 40,
                "release_tag": "customer-news-release/124-promote-" + "b" * 40,
                "mode": "promote"
            }
        }
    ]))
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        [str(DEPLOYMENT_LEDGER), "snapshot"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GH_TOKEN": "test-token",
        },
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    snapshot = json.loads(completed.stdout)
    assert snapshot[0]["deployment_id"] == 123
    assert snapshot[0]["superseded_by"] is None
    assert snapshot[1]["deployment_id"] == 124
    assert snapshot[1]["superseded_by"] == "b" * 40


def test_success_is_published_only_after_distinct_read_only_runtime_proof():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    promote_steps = {step["name"]: step for step in workflow["jobs"]["promote"]["steps"]}
    proof_job = workflow["jobs"]["prove"]
    proof_steps = {step["name"]: step for step in proof_job["steps"]}

    assert "Mark GitHub Deployment successful" not in promote_steps
    assert proof_job["needs"] == "promote"
    assert proof_job["environment"] == "production"
    assert proof_job["permissions"] == {
        "contents": "read",
        "deployments": "write",
        "id-token": "write",
    }
    auth = proof_steps["Authenticate as read-only runtime proof"]
    assert auth["with"]["workload_identity_provider"] == "${{ vars.GCP_PROOF_WIF_PROVIDER }}"
    assert auth["with"]["service_account"] == "${{ vars.GCP_PROOF_SERVICE_ACCOUNT }}"
    commands = "\n".join(str(step.get("run", "")) for step in proof_job["steps"])
    assert "repos/UplixSEO/Uplix-Agents/contents/customer-news/config/cloudbuild_deploy_inventory.yaml" in commands
    assert "scripts/runtime_release_proof.py" in commands
    assert "expected == 24" not in commands
    assert ".missing == 0" in commands
    assert ".mismatched == 0" in commands
    assert ".unknown == 0" in commands
    assert ".skipped == 0" in commands
    assert proof_steps["Mark GitHub Deployment successful"]["if"] == "success()"


def test_runtime_proof_script_is_dynamic_and_read_only():
    script = RUNTIME_PROOF.read_text(encoding="utf-8")

    assert 'row.get("deployment") == "automatic"' in script
    assert 'row.get("environment") == "prod"' in script
    assert 'row.get("environment") == "dev"' in script
    assert "bigquery_sql" in script
    assert "cloud_run_service" in script
    assert "cloud_run_job" in script
    assert "cloud_function" in script
    assert "cloud_build_trigger" in script
    assert "expected" in script
    assert "missing" in script
    assert "mismatched" in script
    assert "unknown" in script
    assert "skipped" in script
    for mutation in (
        "gcloud builds submit",
        "gcloud builds triggers run",
        "gcloud run deploy",
        "gcloud run jobs deploy",
        "gcloud functions deploy",
        "bq query",
        "gcloud storage cp",
    ):
        assert mutation not in script


def test_release_batch_waiter_is_read_only_and_uses_exact_state_validator():
    script = WAIT_RELEASE_BATCH.read_text(encoding="utf-8")

    assert "gcloud builds list" in script
    assert "--page-size=1000" in script
    assert "scripts/release_build_state.py" in script
    assert "--phase" in script
    assert 'if [[ "${ready}" == "2" ]]' in script
    assert "terminal failure" in script
    forbidden = (
        "gcloud builds submit",
        "gcloud builds triggers run",
        "gcloud beta builds approve",
        "gcloud alpha builds approve",
        "gcloud builds cancel",
    )
    assert all(marker not in script for marker in forbidden)


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
    assert "commits/${EXPECTED_SHA}/pulls" in script
    assert '.base.ref == "main"' in script
    assert ".merge_commit_sha == $sha" in script


def _extract_promotion_pr_jq(script: str) -> str:
    start = script.index("[ .[]")
    end = script.index("\n  '", start)
    return script[start:end]


def test_upstream_verifier_accepts_unique_same_tree_main_merge_without_dev_head() -> None:
    script = VERIFY_UPSTREAM.read_text(encoding="utf-8")
    program = _extract_promotion_pr_jq(script)
    pulls = (ROOT / "tests" / "fixtures" / "upstream_commit_pulls.json").read_text(
        encoding="utf-8"
    )
    selected = subprocess.check_output(
        [
            "jq",
            "-c",
            "--arg",
            "sha",
            "2a24624e53252bb692572f2e9d940239d1f06b04",
            "--arg",
            "repository",
            "UplixSEO/Uplix-Agents",
            program,
        ],
        input=pulls,
        text=True,
    )
    parsed = json.loads(selected)
    assert len(parsed) == 1
    assert parsed[0]["number"] == 523
    assert parsed[0]["head"]["ref"] == "release-dev-into-main-immutable-bulk-start"


def test_upstream_verifier_still_selects_classic_dev_to_main_merge() -> None:
    script = VERIFY_UPSTREAM.read_text(encoding="utf-8")
    program = _extract_promotion_pr_jq(script)
    pulls = (ROOT / "tests" / "fixtures" / "upstream_commit_pulls.json").read_text(
        encoding="utf-8"
    )
    selected = subprocess.check_output(
        [
            "jq",
            "-c",
            "--arg",
            "sha",
            "b89e02359ebec3deab8e6e8fc0f3cd79b98bca88",
            "--arg",
            "repository",
            "UplixSEO/Uplix-Agents",
            program,
        ],
        input=pulls,
        text=True,
    )
    parsed = json.loads(selected)
    assert len(parsed) == 1
    assert parsed[0]["number"] == 521
    assert parsed[0]["head"]["ref"] == "dev"


def test_upstream_verifier_rejects_non_unique_main_merge() -> None:
    script = VERIFY_UPSTREAM.read_text(encoding="utf-8")
    program = _extract_promotion_pr_jq(script)
    sha = "2a24624e53252bb692572f2e9d940239d1f06b04"
    duplicate = {
        "number": 999,
        "state": "closed",
        "merged_at": "2026-08-25T21:05:00Z",
        "merge_commit_sha": sha,
        "base": {"ref": "main", "repo": {"full_name": "UplixSEO/Uplix-Agents"}},
        "head": {"ref": "dev", "repo": {"full_name": "UplixSEO/Uplix-Agents"}},
    }
    pulls = json.loads(
        (ROOT / "tests" / "fixtures" / "upstream_commit_pulls.json").read_text(
            encoding="utf-8"
        )
    )
    pulls.append(duplicate)
    selected = subprocess.check_output(
        [
            "jq",
            "-c",
            "--arg",
            "sha",
            sha,
            "--arg",
            "repository",
            "UplixSEO/Uplix-Agents",
            program,
        ],
        input=json.dumps(pulls),
        text=True,
    )
    parsed = json.loads(selected)
    assert len(parsed) == 2


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
    assert "gcloud alpha builds approve" in script
    assert "--page-size=1000" in script
    assert "gcloud builds submit" not in script
    assert "gcloud builds triggers run" not in script

    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    setup = next(
        step
        for step in workflow["jobs"]["promote"]["steps"]
        if step["name"] == "Set up Google Cloud CLI"
    )
    assert setup["with"]["install_components"] == "alpha"


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
elif args[:3] == ["alpha", "builds", "approve"]:
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
    tag = "customer-news-release/123-promote-" + "a" * 40
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
        "RELEASE_TAG": "customer-news-release/123-promote-" + "a" * 40,
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
    assert set(approvals) == {
        f"projects/customer-news-475010/locations/europe-west1/builds/build-{index}"
        for index in range(17)
    }


def test_approval_script_rejects_unknown_release_build(tmp_path):
    completed, approval_log = _run_approver(
        tmp_path, _pending_builds(extra_trigger="unmanaged-prod")
    )

    assert completed.returncode != 0
    assert "unknown builds" in completed.stderr
    assert not approval_log.exists()


def test_approval_script_resumes_started_batch_without_reapproving_builds(tmp_path):
    builds = _pending_builds()
    builds[1]["status"] = "WORKING"
    for row in builds[2:]:
        row["status"] = "SUCCESS"

    completed, approval_log = _run_approver(tmp_path, builds)

    assert completed.returncode == 0, completed.stderr
    assert approval_log.read_text(encoding="utf-8").splitlines() == [
        "projects/customer-news-475010/locations/europe-west1/builds/build-0"
    ]
    assert "already-started" in completed.stdout
    assert "already-successful" in completed.stdout


def test_approval_script_rejects_resumed_batch_with_terminal_failure(tmp_path):
    builds = _pending_builds()
    builds[0]["status"] = "FAILURE"

    completed, approval_log = _run_approver(tmp_path, builds)

    assert completed.returncode != 0
    assert "terminal failure" in completed.stderr
    assert not approval_log.exists()
