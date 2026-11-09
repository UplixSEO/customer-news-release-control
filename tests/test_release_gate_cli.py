import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_release_gate.sh"


def _run_payload(*, steps, status, reference_instant, deployment_status="waiting"):
    return {
        "mode": "run",
        "reference_instant": reference_instant,
        "wait_timer_minutes": 15,
        "grace_seconds": 120,
        "run": {
            "id": 42420364333,
            "status": status,
            "conclusion": None,
            "created_at": "2026-11-07T22:23:07Z",
        },
        "job": {
            "name": "Validate and approve fixed release batch",
            "status": "waiting" if not steps else "completed",
            "conclusion": None,
            "created_at": "2026-11-07T22:23:07Z",
            "started_at": "2026-11-07T22:23:07Z",
            "steps": steps,
        },
        "deployments": [
            {
                "id": 9001,
                "environment": "production",
                "created_at": "2026-11-07T22:23:07Z",
                "statuses": [
                    {"state": deployment_status, "created_at": "2026-11-07T22:23:08Z"}
                ],
            }
        ],
        "pending_deployments": [],
    }


FIXTURES = {
    "gate-timer-pending": (
        _run_payload(
            steps=[],
            status="in_progress",
            reference_instant="2026-11-07T22:28:07Z",
        ),
        "gate-timer-pending",
        0,
    ),
    "gate-admitted-late": (
        _run_payload(
            steps=[{"name": "Set up job", "started_at": "2026-11-07T22:43:07Z"}],
            status="completed",
            reference_instant="2026-11-07T23:00:00Z",
        ),
        "gate-admitted-late",
        3,
    ),
    "gate-stuck": (
        _run_payload(
            steps=[],
            status="in_progress",
            reference_instant="2026-11-07T22:43:07Z",
            deployment_status="queued",
        ),
        "gate-stuck",
        3,
    ),
    "no-deployment": (
        {
            "mode": "deployment",
            "reference_instant": "2026-11-09T00:00:00Z",
            "deployment": None,
        },
        "no-deployment",
        2,
    ),
}


@pytest.mark.parametrize(
    ("name", "expected_verdict", "expected_exit"),
    [(name, fixture[1], fixture[2]) for name, fixture in FIXTURES.items()],
)
def test_fixture_mode_reports_verdict_and_exit_code(
    tmp_path, name, expected_verdict, expected_exit
):
    fixture = tmp_path / f"{name}.json"
    fixture.write_text(json.dumps(FIXTURES[name][0]), encoding="utf-8")

    completed = subprocess.run(
        ["bash", str(SCRIPT), "--fixture", str(fixture)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == expected_exit, completed.stderr
    assert completed.stdout.splitlines()[-1] == expected_verdict
