import importlib.util
from pathlib import Path
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
        release_tag=f"customer-news-release/2-{sha}",
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
    tag = f"customer-news-release/2-{sha}"

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
    tag = f"customer-news-release/2-{sha}"

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
        release_tag=f"customer-news-release/1-{old}",
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
            release_tag=f"customer-news-release/1-{old}",
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
            release_tag=f"customer-news-release/3-{old}",
            mode="rollback",
            entries=[_entry(10, current, f"customer-news-release/2-{current}", "success")],
            ancestry={(old, current): True},
            compatibility_approved=False,
            rollback_reason="UPX-895 exercise",
        )


def test_compatibility_approved_ancestor_rollback_creates_new_epoch_entry():
    ledger = _load_module()
    old = "a" * 40
    current = "b" * 40
    rollback_tag = f"customer-news-release/3-{old}"

    decision = ledger.decide_release(
        candidate_sha=old,
        current_head_sha=current,
        release_tag=rollback_tag,
        mode="rollback",
        entries=[
            _entry(9, old, f"customer-news-release/1-{old}", "inactive"),
            _entry(10, current, f"customer-news-release/2-{current}", "success"),
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
        _entry(1, old, f"customer-news-release/1-{old}", "success"),
        _entry(2, unrelated, f"customer-news-release/1-{unrelated}", "failure"),
        _entry(3, current, f"customer-news-release/2-{current}", "in_progress"),
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
    ) == [{"deployment_id": 1, "superseded_by": current}]
