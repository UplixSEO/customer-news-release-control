import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_ledger.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("release_ledger", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("Unable to load release ledger module")
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry(deployment_id, sha, release_tag, state, mode="promote"):
    return {
        "deployment_id": deployment_id,
        "sha": sha,
        "release_tag": release_tag,
        "state": state,
        "mode": mode,
    }


def test_current_head_without_existing_entry_creates_pending_deployment():
    ledger = _load_module()
    sha = "b" * 40

    decision = ledger.decide_release(
        candidate_sha=sha,
        current_head_sha=sha,
        release_tag=f"customer-news-release/2-promote-{sha}",
        mode="promote",
        entries=[],
        ancestry={},
    )

    assert decision == {
        "action": "create",
        "deployment_id": None,
        "mode": "promote",
        "sha": sha,
        "superseded_by": None,
    }


@pytest.mark.parametrize("state", ["pending", "in_progress", "failure"])
def test_interrupted_release_resumes_same_deployment_without_blind_redeploy(state):
    ledger = _load_module()
    sha = "b" * 40
    tag = f"customer-news-release/2-promote-{sha}"

    decision = ledger.decide_release(
        candidate_sha=sha,
        current_head_sha=sha,
        release_tag=tag,
        mode="promote",
        entries=[_entry(73, sha, tag, state)],
        ancestry={},
    )

    assert decision["action"] == "resume"
    assert decision["deployment_id"] == 73


def test_successful_exact_release_is_repaired_without_redeploy():
    ledger = _load_module()
    sha = "b" * 40
    tag = f"customer-news-release/2-promote-{sha}"

    decision = ledger.decide_release(
        candidate_sha=sha,
        current_head_sha=sha,
        release_tag=tag,
        mode="promote",
        entries=[_entry(73, sha, tag, "success")],
        ancestry={},
    )

    assert decision["action"] == "already_succeeded"
    assert decision["deployment_id"] == 73


def test_stale_candidate_is_superseded_only_by_proven_current_descendant():
    ledger = _load_module()
    old = "a" * 40
    new = "b" * 40

    decision = ledger.decide_release(
        candidate_sha=old,
        current_head_sha=new,
        release_tag=f"customer-news-release/1-promote-{old}",
        mode="promote",
        entries=[],
        ancestry={(old, new): True},
    )

    assert decision["action"] == "superseded"
    assert decision["superseded_by"] == new


def test_diverged_candidate_is_rejected_not_superseded():
    ledger = _load_module()
    old = "a" * 40
    other = "b" * 40

    with pytest.raises(ledger.LedgerError, match="not an ancestor"):
        ledger.decide_release(
            candidate_sha=old,
            current_head_sha=other,
            release_tag=f"customer-news-release/1-promote-{old}",
            mode="promote",
            entries=[],
            ancestry={(old, other): False},
        )


def test_rollback_requires_compatibility_approval_and_reason():
    ledger = _load_module()
    old = "a" * 40
    current = "b" * 40

    with pytest.raises(ledger.LedgerError, match="compatibility-approved"):
        ledger.decide_release(
            candidate_sha=old,
            current_head_sha=current,
            release_tag=f"customer-news-release/3-rollback-{old}",
            mode="rollback",
            entries=[_entry(10, current, f"customer-news-release/2-promote-{current}", "success")],
            ancestry={(old, current): True},
            compatibility_approved=False,
            rollback_reason="UPX-895 exercise",
        )


def test_compatibility_approved_ancestor_rollback_creates_new_epoch_entry():
    ledger = _load_module()
    old = "a" * 40
    current = "b" * 40
    rollback_tag = f"customer-news-release/3-rollback-{old}"

    decision = ledger.decide_release(
        candidate_sha=old,
        current_head_sha=current,
        release_tag=rollback_tag,
        mode="rollback",
        entries=[
            _entry(9, old, f"customer-news-release/1-promote-{old}", "inactive"),
            _entry(10, current, f"customer-news-release/2-promote-{current}", "success"),
        ],
        ancestry={(old, current): True},
        compatibility_approved=True,
        rollback_reason="UPX-895 compatibility approval",
    )

    assert decision["action"] == "create"
    assert decision["mode"] == "rollback"
    assert decision["sha"] == old


def test_supersession_targets_are_selected_only_after_new_success():
    ledger = _load_module()
    old = "a" * 40
    current = "b" * 40
    unrelated = "c" * 40
    entries = [
        _entry(1, old, f"customer-news-release/1-promote-{old}", "success"),
        _entry(2, unrelated, f"customer-news-release/1-promote-{unrelated}", "failure"),
        _entry(3, current, f"customer-news-release/2-promote-{current}", "in_progress"),
        _entry(4, old, f"customer-news-release/3-promote-{old}", "in_progress"),
    ]

    assert ledger.supersession_targets(
        successful_sha=current,
        entries=entries,
        ancestry={(old, current): True, (unrelated, current): False},
        release_succeeded=False,
    ) == []
    assert ledger.supersession_targets(
        successful_sha=current,
        entries=entries,
        ancestry={(old, current): True, (unrelated, current): False},
        release_succeeded=True,
    ) == [
        {"deployment_id": 1, "superseded_by": current},
        {"deployment_id": 4, "superseded_by": current},
    ]


def test_auto_promote_skips_when_no_candidate_tag():
    ledger = _load_module()

    decision = ledger.auto_promote_decision(
        head_sha="b" * 40, candidate_tag="", entries=[]
    )

    assert decision == {"dispatch": False, "reason": "no_candidate"}


def test_auto_promote_dispatches_first_release_with_empty_ledger():
    ledger = _load_module()
    sha = "b" * 40

    decision = ledger.auto_promote_decision(
        head_sha=sha,
        candidate_tag=f"customer-news-release/2-promote-{sha}",
        entries=[],
    )

    assert decision == {"dispatch": True, "reason": "candidate_ready"}


def test_auto_promote_dispatches_new_candidate_over_older_accepted_release():
    ledger = _load_module()
    prior = "a" * 40
    head = "b" * 40

    decision = ledger.auto_promote_decision(
        head_sha=head,
        candidate_tag=f"customer-news-release/9-promote-{head}",
        entries=[
            _entry(1, prior, f"customer-news-release/7-promote-{prior}", "success"),
        ],
    )

    assert decision == {"dispatch": True, "reason": "candidate_ready"}


def test_auto_promote_skips_when_head_is_already_accepted():
    ledger = _load_module()
    head = "b" * 40

    decision = ledger.auto_promote_decision(
        head_sha=head,
        candidate_tag=f"customer-news-release/9-promote-{head}",
        entries=[
            _entry(1, head, f"customer-news-release/5-promote-{head}", "success"),
        ],
    )

    assert decision == {"dispatch": False, "reason": "already_accepted"}


def test_auto_promote_skips_stale_head_after_accepted_rollback_epoch():
    ledger = _load_module()
    ancestor = "a" * 40
    head = "b" * 40

    decision = ledger.auto_promote_decision(
        head_sha=head,
        candidate_tag=f"customer-news-release/5-promote-{head}",
        entries=[
            _entry(1, head, f"customer-news-release/5-promote-{head}", "failure"),
            _entry(
                2,
                ancestor,
                f"customer-news-release/7-rollback-{ancestor}",
                "success",
                mode="rollback",
            ),
        ],
    )

    assert decision == {
        "dispatch": False,
        "reason": "accepted_epoch_supersedes_candidate",
    }


def test_auto_promote_dispatches_resume_for_failed_head_without_newer_acceptance():
    ledger = _load_module()
    head = "b" * 40
    tag = f"customer-news-release/5-promote-{head}"

    decision = ledger.auto_promote_decision(
        head_sha=head,
        candidate_tag=tag,
        entries=[_entry(1, head, tag, "failure")],
    )

    assert decision == {"dispatch": True, "reason": "candidate_ready"}


def test_auto_promote_rejects_candidate_tag_for_different_sha():
    ledger = _load_module()

    with pytest.raises(
        ledger.LedgerError, match="promote tag for the exact upstream head"
    ):
        ledger.auto_promote_decision(
            head_sha="b" * 40,
            candidate_tag=f"customer-news-release/5-promote-{'c' * 40}",
            entries=[],
        )


def test_auto_promote_rejects_rollback_candidate_tag():
    ledger = _load_module()
    sha = "b" * 40

    with pytest.raises(
        ledger.LedgerError, match="promote tag for the exact upstream head"
    ):
        ledger.auto_promote_decision(
            head_sha=sha,
            candidate_tag=f"customer-news-release/5-rollback-{sha}",
            entries=[],
        )


def test_auto_promote_rejects_unparseable_candidate_tag():
    ledger = _load_module()
    sha = "b" * 40

    with pytest.raises(
        ledger.LedgerError, match="promote tag for the exact upstream head"
    ):
        ledger.auto_promote_decision(
            head_sha=sha,
            candidate_tag=f"customer-news-release/0-promote-{sha}",
            entries=[],
        )


def test_auto_promote_rejects_malformed_head_sha():
    ledger = _load_module()

    with pytest.raises(ledger.LedgerError, match="exact 40-hex value"):
        ledger.auto_promote_decision(
            head_sha="not-a-sha", candidate_tag="", entries=[]
        )


def test_auto_promote_cli_operation_consumes_snapshot_payload():
    sha = "b" * 40
    payload = {
        "head_sha": sha,
        "candidate_tag": f"customer-news-release/2-promote-{sha}",
        "entries": [],
    }

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "auto-promote"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(completed.stdout) == {
        "dispatch": True,
        "reason": "candidate_ready",
    }
