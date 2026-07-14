import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "runtime_release_proof.py"
SHA = "a" * 40


def _load_module():
    spec = importlib.util.spec_from_file_location("runtime_release_proof", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("Unable to load runtime release proof module")
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_bigquery_head_places_global_json_format_before_subcommand(
    monkeypatch, tmp_path, capsys
):
    proof = _load_module()
    inventory = tmp_path / "inventory.json"
    accepted = tmp_path / "accepted.json"
    inventory.write_text(json.dumps({"targets": []}), encoding="utf-8")
    accepted.write_text("[]", encoding="utf-8")
    calls = []

    def fake_run_json(args):
        calls.append(args)
        return []

    monkeypatch.setattr(proof, "run_json", fake_run_json)

    assert (
        proof.main(
            [
                "--inventory-json",
                str(inventory),
                "--accepted-builds-json",
                str(accepted),
                "--expected-sha",
                SHA,
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert calls[0][:5] == [
        "bq",
        "--headless=true",
        "--quiet=true",
        "--format=json",
        "head",
    ]


def test_run_json_accepts_complete_document_after_wif_preamble(monkeypatch):
    proof = _load_module()
    completed = subprocess.CompletedProcess(
        args=["bq"],
        returncode=0,
        stdout='Hosted credential notice\n[{"target_id":"core-ddl-prod"}]\n',
        stderr="",
    )
    monkeypatch.setattr(proof.subprocess, "run", lambda *args, **kwargs: completed)

    assert proof.run_json(["bq", "head"]) == [{"target_id": "core-ddl-prod"}]


def test_run_json_rejects_non_whitespace_after_document(monkeypatch):
    proof = _load_module()
    completed = subprocess.CompletedProcess(
        args=["bq"],
        returncode=0,
        stdout='notice\n[{"target_id":"core-ddl-prod"}]\ntrailing output',
        stderr="",
    )
    monkeypatch.setattr(proof.subprocess, "run", lambda *args, **kwargs: completed)

    with pytest.raises(proof.ProofError, match="invalid JSON"):
        proof.run_json(["bq", "head"])
