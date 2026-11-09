import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_gate_state.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("release_gate_state", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("Unable to load release gate state module")
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _deployment(
    *,
    deployment_id=9001,
    created_at="2026-11-07T22:23:07Z",
    status="waiting",
    status_at="2026-11-07T22:23:08Z",
):
    return {
        "id": deployment_id,
        "environment": "production",
        "created_at": created_at,
        "statuses": [{"state": status, "created_at": status_at}],
    }


def _run_payload(
    *,
    created_at="2026-11-07T22:23:07Z",
    steps=None,
    status="in_progress",
    conclusion=None,
    deployments=None,
    reference_instant="2026-11-07T22:23:12Z",
):
    return {
        "mode": "run",
        "reference_instant": reference_instant,
        "wait_timer_minutes": 15,
        "grace_seconds": 120,
        "run": {
            "id": 42420364333,
            "status": status,
            "conclusion": conclusion,
            "created_at": created_at,
        },
        "job": {
            "name": "Validate and approve fixed release batch",
            "status": "waiting" if not steps else "completed",
            "conclusion": conclusion,
            "created_at": created_at,
            "started_at": created_at,
            "steps": steps or [],
        },
        "deployments": [_deployment()] if deployments is None else deployments,
        "pending_deployments": [],
    }


def _ledger_payload(*, statuses, reference_instant, deployment_id=7627040397):
    return {
        "mode": "deployment",
        "reference_instant": reference_instant,
        "deployment": {
            "id": deployment_id,
            "environment": "customer-news-runtime",
            "created_at": "2026-11-07T21:06:58Z",
            "statuses": statuses,
        },
    }


def test_gate_timer_pending_at_five_minutes():
    state = _load_module()
    deployment = _deployment(
        created_at="2026-11-09T08:14:54Z", status_at="2026-11-09T08:14:54Z"
    )
    payload = _run_payload(
        created_at="2026-11-09T08:14:53Z",
        deployments=[deployment],
        reference_instant="2026-11-09T08:19:54Z",
    )

    result = state.classify(payload)

    assert result["verdict"] == "gate-timer-pending"
    assert state.EXIT_CODES[result["verdict"]] == 0
    assert result["elapsed_seconds"] == 300


def test_gate_admitted_at_fifteen_minutes_four_seconds():
    state = _load_module()
    payload = _run_payload(
        steps=[{"name": "Set up job", "started_at": "2026-11-07T22:38:10Z"}],
        status="completed",
        conclusion="success",
        reference_instant="2026-11-07T22:45:00Z",
    )

    result = state.classify(payload)

    assert result["verdict"] == "gate-admitted"
    assert state.EXIT_CODES[result["verdict"]] == 0
    assert result["admission_seconds"] == 903


def test_gate_admitted_late_at_twenty_minutes():
    state = _load_module()
    payload = _run_payload(
        steps=[{"name": "Set up job", "started_at": "2026-11-07T22:43:07Z"}],
        status="completed",
        conclusion="success",
        reference_instant="2026-11-07T23:00:00Z",
    )

    result = state.classify(payload)

    assert result["verdict"] == "gate-admitted-late"
    assert state.EXIT_CODES[result["verdict"]] == 3
    assert result["admission_seconds"] == 1200


def test_gate_stuck_queued_past_bound_without_step():
    state = _load_module()
    deployment = _deployment(status="queued", status_at="2026-11-07T22:23:08Z")
    payload = _run_payload(
        deployments=[deployment], reference_instant="2026-11-07T22:43:07Z"
    )

    result = state.classify(payload)

    assert result["verdict"] == "gate-stuck"
    assert state.EXIT_CODES[result["verdict"]] == 3
    assert result["deployment_status"] == "queued"


def test_run_terminal_residual_cancelled_zero_steps():
    state = _load_module()
    deployment = _deployment(
        deployment_id=7626509008,
        created_at="2026-11-07T20:19:08Z",
        status="error",
        status_at="2026-11-07T20:24:41Z",
    )
    payload = _run_payload(
        created_at="2026-11-07T20:19:08Z",
        status="completed",
        conclusion="cancelled",
        deployments=[deployment],
        reference_instant="2026-11-07T22:00:00Z",
    )

    result = state.classify(payload)

    assert result["verdict"] == "run-terminal-residual"
    assert state.EXIT_CODES[result["verdict"]] == 0


def test_ledger_open_resumable_at_reference_instant():
    state = _load_module()
    payload = _ledger_payload(
        reference_instant="2026-11-07T21:39:31Z",
        statuses=[
            {"state": "inactive", "created_at": "2026-11-07T23:00:13Z"},
            {"state": "in_progress", "created_at": "2026-11-07T21:39:31Z"},
            {"state": "in_progress", "created_at": "2026-11-07T21:06:59Z"},
        ],
    )

    result = state.classify(payload)

    assert result["verdict"] == "ledger-open-record"
    assert result["sub"] == "resumable"
    assert state.EXIT_CODES[result["verdict"]] == 0


def test_ledger_open_superseded_only_for_error_status():
    state = _load_module()
    payload = _ledger_payload(
        reference_instant="2026-11-07T22:00:00Z",
        statuses=[{"state": "error", "created_at": "2026-11-07T21:06:59Z"}],
    )

    result = state.classify(payload)

    assert result["verdict"] == "ledger-open-record"
    assert result["sub"] == "superseded-only"
    assert state.EXIT_CODES[result["verdict"]] == 0


def test_ledger_terminal_today():
    state = _load_module()
    payload = _ledger_payload(
        reference_instant="2026-11-09T00:00:00Z",
        statuses=[
            {"state": "inactive", "created_at": "2026-11-07T23:00:13Z"},
            {"state": "in_progress", "created_at": "2026-11-07T21:39:31Z"},
            {"state": "in_progress", "created_at": "2026-11-07T21:06:59Z"},
        ],
    )

    result = state.classify(payload)

    assert result["verdict"] == "ledger-terminal"
    assert state.EXIT_CODES[result["verdict"]] == 0


def test_no_deployment_reports_missing_record_reasons():
    state = _load_module()
    outside_window = _deployment(created_at="2026-11-07T22:33:07Z")
    run_result = state.classify(
        _run_payload(deployments=[outside_window], reference_instant="2026-11-07T22:35:00Z")
    )
    ledger_result = state.classify(
        {
            "mode": "deployment",
            "reference_instant": "2026-11-09T00:00:00Z",
            "deployment": None,
        }
    )

    assert run_result["verdict"] == "no-deployment"
    assert run_result["error"] == "no-production-deployment"
    assert state.EXIT_CODES[run_result["verdict"]] == 2
    assert ledger_result["verdict"] == "no-deployment"
    assert ledger_result["error"] == "no-ledger-record"
    assert state.EXIT_CODES[ledger_result["verdict"]] == 2


def test_ambiguous_production_deployment_lists_candidates():
    state = _load_module()
    payload = _run_payload(
        deployments=[
            _deployment(deployment_id=9001, created_at="2026-11-07T22:23:07Z"),
            _deployment(deployment_id=9002, created_at="2026-11-07T22:23:08Z"),
        ]
    )

    result = state.classify(payload)

    assert result["verdict"] == "no-deployment"
    assert result["error"] == "ambiguous-production-deployment"
    assert result["candidates"] == [9001, 9002]
    assert state.EXIT_CODES[result["verdict"]] == 2


def test_live_queued_deployment_fifteen_minutes_one_second_is_pending():
    state = _load_module()
    deployment = _deployment(
        created_at="2026-11-09T08:14:54Z",
        status="queued",
        status_at="2026-11-09T08:14:54Z",
    )
    payload = _run_payload(
        created_at="2026-11-09T08:14:53Z",
        deployments=[deployment],
        reference_instant="2026-11-09T08:29:55Z",
    )

    result = state.classify(payload)

    assert result["verdict"] == "gate-timer-pending"
    assert state.EXIT_CODES[result["verdict"]] == 0


def test_error_without_step_is_stuck_regardless_of_elapsed():
    state = _load_module()
    deployment = _deployment(
        created_at="2026-11-07T22:23:07Z",
        status="error",
        status_at="2026-11-07T22:23:08Z",
    )
    payload = _run_payload(
        deployments=[deployment], reference_instant="2026-11-07T22:24:07Z"
    )

    result = state.classify(payload)

    assert result["verdict"] == "gate-stuck"
    assert state.EXIT_CODES[result["verdict"]] == 3
