from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from risk_agent.collection.formal_features import extract_decision_features
from risk_agent.prediction.train_evidence import TrainReader, canonical, digest, load_train, validate_cutoff
from risk_agent.prediction.train_signal_audit import build_audit, distribution, relationships, signals, validate_candidate


def fixture_train(root):
    """Synthetic local fixture only. No real runs or external services."""
    base = root / "runs/train/run"
    def put(name, value, lines=False):
        path = base / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n")
        return path
    run = "fixture_run"
    put("run_manifest.json", {"authorized_split": "train", "run_id": run, "formal_experiment": True, "offline_fixture": False})
    call = {"name": "lookup", "arguments": {}, "id": "synthetic_call"}
    initial = [{"role": "user", "content": "Task"}]
    action = {"type": "action", "step_number": 1, "timing": {"start_time": 1609459200, "end_time": 1609459202},
        "tool_calls": [call], "model_input_messages": initial, "is_final_answer": False}
    messages = [{"role": "system", "content": "lookup: Read a value"}, {"role": "user", "content": "Task"},
                {"role": "tool-response", "content": "Observation:\n42"}]
    payload = {"agent": {"task": "Task", "step_number": 1, "remaining_steps": 9},
        "memory": {"steps": [action]}, "canonical_next_model_input": messages,
        "model": {"generation": {"max_tokens": 1024}},
        "tool_registry": [{"name": "lookup", "inputs": {}, "description": "Read a value", "output_type": "any"}]}
    payload["next_model_input_hash"] = canonical(messages)
    budget = {"max_tokens": 50000, "max_tool_calls": 12, "max_latency_seconds": 900,
        "latency_seconds_used": 2, "prompt_tokens_used": 100, "completion_tokens_used": 10,
        "tool_calls_used": 1, "model_calls_used": 1, "max_model_calls": 10}
    models = [{"input_hash": canonical(initial), "status": "succeeded", "prompt_tokens": 100,
               "completion_tokens": 10, "latency_seconds": 1}]
    tools = [{"tool_name": "lookup", "tool_arguments": {}, "latency_seconds": .1,
              "timestamp": datetime.fromtimestamp(1609459201, timezone.utc).isoformat()}]
    packet = extract_decision_features(agent_payload=payload, budget={**budget, "tokens_used": 110},
        source_model_records=models, source_tool_records=tools)
    prefix = "source/checkpoints/cp"
    put(prefix + "/agent/agent_state.json", {"payload": payload})
    put(prefix + "/runtime/budget_state.json", budget)
    put(prefix + "/runtime/execution_position.json", {"next_operation": "model_generation", "phase": "after_tool_observation_before_model_generation"})
    put("tasks/task/source_model_requests.json", models)
    put("tasks/task/source_tools.jsonl", tools[0], True)
    put("tasks/task/decision_features.json", packet)
    common = {"run_id": run, "task_id": "task", "template_id": "template", "checkpoint_id": "cp", "split": "train",
              "previous_record_sha256": None}
    cp = {**common, "checkpoint_ref": prefix, "hashes": {"agent_state_hash": canonical(payload), "next_model_input_hash": canonical(messages)}}
    cp["record_sha256"] = canonical(cp)
    risk = {**common, "formal_experiment": True, "offline_fixture": False, "future_data_excluded": True,
        "label": 1, "checkpoint_record_sha256": cp["record_sha256"],
        "feature_provenance_ref": "tasks/task/decision_features.json", "feature_names": packet["feature_names"],
        "features": packet["values"], "features_sha256": packet["features_sha256"],
        "provenance_sha256": canonical(packet["provenance"])}
    risk["record_sha256"] = canonical(risk)
    put("records/checkpoints.jsonl", cp, True); put("records/risk_candidates.jsonl", risk, True)
    release = put("train_eligibility_release.json", {"split": "train", "formal_experiment": True,
        "research_dataset_eligible": True, "censored_task_ids": [], "risk_record_references": [
            {"run_id": run, "task_id": "task", "record_sha256": risk["record_sha256"]}]})
    entries = [{"path": str(p.relative_to(base)), "size_bytes": p.stat().st_size, "sha256": digest(p.read_bytes())}
               for p in base.rglob("*") if p.is_file()]
    manifest = put("artifact_manifest.json", {"files": entries})
    spec = {"run_id": run, "directory": str(base), "artifact_manifest": "artifact_manifest.json",
            "artifact_manifest_sha256": digest(manifest.read_bytes())}
    proof = root / "runs/proof/train_proof.json"; proof.parent.mkdir(parents=True)
    proof.write_text(json.dumps({"runs": [spec], "release_path": str(release), "release_sha256": digest(release.read_bytes())}))
    return proof, spec, payload, budget, models, tools


def test_real_loader_path_with_synthetic_train_fixture(tmp_path):
    proof, _, _, _, _, _ = fixture_train(tmp_path)
    rows, audit = load_train(tmp_path, proof, digest(proof.read_bytes()))
    assert len(rows) == 1 and audit["future_outcomes_loaded"] is False
    original, candidates, _ = build_audit(rows)
    assert len(original["features"]) == 18 and len(candidates["candidates"]) == 12
    assert {c["recommendation"] for c in candidates["candidates"]} <= {"CANDIDATE", "CONDITIONAL", "REJECT"}


@pytest.mark.parametrize("split", ["validation", "test"])
def test_other_split_directories_rejected_before_open(tmp_path, monkeypatch, split):
    def deny(*args): raise AssertionError("No file may be opened")
    monkeypatch.setattr(Path, "read_bytes", deny)
    with pytest.raises(ValueError, match="Validation/Test"):
        TrainReader(tmp_path, {"directory": str(tmp_path / "runs" / split / "run")})


def test_declared_validation_in_train_named_directory_rejected(tmp_path):
    _, spec, *_ = fixture_train(tmp_path)
    path = Path(spec["directory"]) / "run_manifest.json"
    value = json.loads(path.read_text()); value["authorized_split"] = "validation"; path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Train identity"): TrainReader(tmp_path, spec)


def test_manifest_and_member_hash_mismatches_fail_closed(tmp_path):
    _, spec, *_ = fixture_train(tmp_path)
    bad = {**spec, "artifact_manifest_sha256": "0" * 64}
    with pytest.raises(ValueError, match="manifest hash"): TrainReader(tmp_path, bad)
    reader = TrainReader(tmp_path, spec)
    (Path(spec["directory"]) / "source/checkpoints/cp/runtime/budget_state.json").write_text("{}")
    with pytest.raises(ValueError, match="artifact hash"): reader.read("source/checkpoints/cp/runtime/budget_state.json")


@pytest.mark.parametrize("name", ["records/continues.jsonl", "continue/branches/outcome.json",
    "source/checkpoints/cp/runtime/fault_adapter_state.json", "source/checkpoints/cp/environment/mock_api_state.json"])
def test_future_and_hidden_artifacts_cannot_be_read(tmp_path, name):
    _, spec, *_ = fixture_train(tmp_path)
    reader = TrainReader(tmp_path, spec)
    with pytest.raises(ValueError, match="hidden"): reader.read(name)


def test_duplicate_source_read_is_idempotent_not_extra_samples(tmp_path):
    proof, *_ = fixture_train(tmp_path)
    value = json.loads(proof.read_text()); value["runs"] *= 2
    proof.write_text(json.dumps(value))
    rows, _ = load_train(tmp_path, proof, digest(proof.read_bytes()))
    assert len(rows) == 1


def test_invalid_record_chain_rejected_inside_reader(tmp_path):
    _, spec, *_ = fixture_train(tmp_path)
    reader = TrainReader(tmp_path, spec)
    rows = reader.read("records/checkpoints.jsonl", lines=True); rows[0]["previous_record_sha256"] = "bad"
    reader.cache["records/checkpoints.jsonl"] = json.dumps(rows[0]).encode() + b"\n"
    with pytest.raises(ValueError, match="record chain"): reader.records("records/checkpoints.jsonl")


@pytest.mark.parametrize("kind", ["extra_model", "late_tool", "future_step", "input_hash"])
def test_nondecision_events_fail_cutoff_validation(tmp_path, kind):
    _, _, payload, budget, models, tools = fixture_train(tmp_path)
    if kind == "extra_model": models.append(deepcopy(models[0]))
    elif kind == "late_tool": tools[0]["timestamp"] = "2099-01-01T00:00:00+00:00"
    elif kind == "future_step": payload["memory"]["steps"][0]["step_number"] = 2
    else: models[0]["input_hash"] = "bad"
    with pytest.raises(ValueError): validate_cutoff(payload, budget, models, tools)


@pytest.mark.parametrize("source", ["fault_type", "future_outcome", "expected_answer"])
def test_hidden_or_future_fields_cannot_enter_candidate_catalog(source):
    c = {"recommendation": "CANDIDATE", "decision_time_available": True,
        "depends_on_future_data": False, "depends_on_hidden_metadata": False,
        "source_field": source, "source_file": [], "source_record_sha256": ["a" * 64]}
    with pytest.raises(ValueError): validate_candidate(c)


def test_descriptive_statistics_and_signed_redundancy():
    d = distribution([1, 2, None]); assert d["non_missing_count"] == 2 and d["mean"] == 1.5
    assert d["std_population"] == .5
    r = relationships({"a": [0, 1, 0], "b": [1, 0, 1], "constant": [1, 1, 1]})
    assert r["high_numeric_correlations"][0]["pearson_r"] == pytest.approx(-1)
    assert distribution([1 / 12] * 95)["std_population"] == 0
    assert not relationships({"constant": [1 / 12] * 95, "changing": list(range(95))})["high_numeric_correlations"]


def test_prompt_paths_and_opaque_ids_are_not_dependency_or_difficulty_signals(tmp_path):
    proof, *_ = fixture_train(tmp_path)
    rows, _ = load_train(tmp_path, proof, digest(proof.read_bytes()))
    from risk_agent.prediction.train_signal_audit import public_projection
    view = public_projection(rows[0])
    view["task_text"] = 'Read data/dependency_then_write_001.csv and return its contents.'
    monitor = {"cutoff_verified": True, "model_requests": [{"completion_tokens": 10}], "completion_limit": 1024}
    first, _ = signals(view, monitor)
    view["task_text"] = 'Read data/ordinary_001.csv and return its contents.'
    second, _ = signals(view, monitor)
    assert first == second and first["prompt_explicit_dependency"] == 0


def test_monitor_rejects_hidden_metadata_and_unverified_cutoff():
    for value in ({"cutoff_verified": False, "model_requests": [], "completion_limit": 1024},
                  {"cutoff_verified": True, "model_requests": [], "completion_limit": 1024, "fault_type": "hidden"}):
        with pytest.raises(ValueError): signals({}, value)


@pytest.mark.parametrize("target", ["runs/phase2_formal_validation/val/records/risk_candidates.jsonl",
    "runs/phase2_formal_test/test/agent/agent_state.json",
    "runs/phase2_formal_train/run/records/continues.jsonl",
    "runs/phase2_formal_train/run/source/checkpoints/cp/runtime/fault_adapter_state.json",
    "docs/phase2/daily_reports/validation_error_report.md"])
def test_execution_read_guard_blocks_actual_open_attempt_in_isolated_process(tmp_path, target):
    # Separate interpreter because an audit hook cannot be removed from pytest.
    import subprocess
    import sys
    code = '''import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('audit_entry',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
root=pathlib.Path(sys.argv[2]);events=[]
module.install_guard(root,root/'runs/proof/train_proof.json',root/'runs/output',events)
try:
    (root/sys.argv[3]).open('rb')
except RuntimeError:
    assert events[-1]['event']=='forbidden_dataset_read'
    print('BLOCKED_AS_REQUIRED')
else:
    raise AssertionError('Forbidden file was opened')
'''
    script = Path(__file__).resolve().parents[1] / "scripts/audit_train_only_public_signals.py"
    result = subprocess.run([sys.executable, "-c", code, str(script), str(tmp_path), target],
        capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "BLOCKED_AS_REQUIRED"
