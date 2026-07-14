import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_build_state.py"
AUTHORITIES = ROOT / "config" / "release-authorities.txt"


def _load_module():
    spec = importlib.util.spec_from_file_location("release_build_state", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("Unable to load release build state module")
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _authorities():
    return [
        line.strip()
        for line in AUTHORITIES.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _builds(status="PENDING", *, missing=None, duplicate=None, unknown=None):
    rows = [
        {"id": f"build-{index}", "trigger": trigger, "status": status}
        for index, trigger in enumerate(_authorities())
        if trigger != missing
    ]
    if duplicate:
        rows.append({"id": "duplicate", "trigger": duplicate, "status": status})
    if unknown:
        rows.append({"id": "unknown", "trigger": unknown, "status": status})
    return rows


def test_exact_pending_batch_is_ready_for_approval():
    state = _load_module()
    result = state.evaluate_batch(_builds(), expected_authorities=_authorities(), phase="pending")

    assert result["ready"] is True
    assert result["expected"] == 17
    assert result["missing"] == []
    assert result["duplicate"] == []
    assert result["unknown"] == []


@pytest.mark.parametrize(
    ("rows", "marker"),
    [
        (_builds(missing=_authorities()[0]), "missing"),
        (_builds(duplicate=_authorities()[0]), "duplicate"),
        (_builds(unknown="unmanaged-prod"), "unknown"),
    ],
)
def test_non_exact_batch_fails_closed(rows, marker):
    state = _load_module()
    result = state.evaluate_batch(rows, expected_authorities=_authorities(), phase="pending")

    assert result["ready"] is False
    assert result[marker]


def test_prior_batch_must_be_fully_terminal_before_next_approval():
    state = _load_module()
    working = _builds(status="SUCCESS")
    working[0]["status"] = "WORKING"

    blocked = state.evaluate_batch(working, expected_authorities=_authorities(), phase="terminal")
    terminal = state.evaluate_batch(
        _builds(status="FAILURE"), expected_authorities=_authorities(), phase="terminal"
    )

    assert blocked["ready"] is False
    assert blocked["nonterminal"] == [working[0]["trigger"]]
    assert terminal["ready"] is True


def test_prior_unapproved_pending_batch_is_quiescent_but_working_is_not():
    state = _load_module()
    pending = state.evaluate_batch(
        _builds(status="PENDING"),
        expected_authorities=_authorities(),
        phase="quiescent",
    )
    mixed = _builds(status="PENDING")
    mixed[0]["status"] = "SUCCESS"
    mixed[1]["status"] = "WORKING"
    active = state.evaluate_batch(
        mixed,
        expected_authorities=_authorities(),
        phase="quiescent",
    )

    assert pending["ready"] is True
    assert active["ready"] is False
    assert active["nonterminal"] == [mixed[1]["trigger"]]


def test_current_batch_requires_all_success_for_release_proof():
    state = _load_module()
    failed = _builds(status="SUCCESS")
    failed[-1]["status"] = "FAILURE"

    result = state.evaluate_batch(failed, expected_authorities=_authorities(), phase="success")

    assert result["ready"] is False
    assert result["failed"] == [failed[-1]["trigger"]]
    assert state.evaluate_batch(
        _builds(status="SUCCESS"), expected_authorities=_authorities(), phase="success"
    )["ready"] is True


def test_success_phase_distinguishes_working_from_terminal_failure():
    state = _load_module()
    working = _builds(status="SUCCESS")
    working[0]["status"] = "WORKING"
    failed = _builds(status="SUCCESS")
    failed[0]["status"] = "FAILURE"

    working_result = state.evaluate_batch(
        working, expected_authorities=_authorities(), phase="success"
    )
    failed_result = state.evaluate_batch(
        failed, expected_authorities=_authorities(), phase="success"
    )

    assert working_result["nonterminal"] == [working[0]["trigger"]]
    assert working_result["failed"] == []
    assert working_result["terminal_failure"] is False
    assert failed_result["nonterminal"] == []
    assert failed_result["failed"] == [failed[0]["trigger"]]
    assert failed_result["terminal_failure"] is True


def test_unrecognized_phase_is_rejected():
    state = _load_module()
    with pytest.raises(state.BuildStateError, match="phase"):
        state.evaluate_batch(_builds(), expected_authorities=_authorities(), phase="deploy")
