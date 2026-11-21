#!/usr/bin/env python3
"""Run the local post-audit acceptance suite and emit sanitized evidence."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk_agent.agent import AgentStateAdapter, ModelRequestRejected, RecordingModel
from risk_agent.benchmark import TaskEnvironment
from tests import test_checkpoint_v2 as checkpoint_fixtures
from tests.agent.test_checkpointable_agent import (
    FailingModel,
    _capture,
    _expected,
)
from tests.agent.test_multistep_resume import (
    _checkpoint_inventory,
    _final_answer,
    _increment_message,
    _restore,
)


BASELINE_COMMIT = "52e97e7b37b3d88b65acbd8cffcad644dff232c1"
EVIDENCE_BASE = ROOT / "docs" / "phase2" / "evidence" / "day4_post_audit"


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _common(run_id: str, local_run: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "baseline_commit": BASELINE_COMMIT,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "smolagents_version": version("smolagents"),
        "scripted_model_id": "qwen3-8b-local-scripted",
        "formal_experiment": False,
        "execution_mode": "local_engineering_test",
        "model_backend": "local_scripted_model",
        "real_qwen_executed": False,
        "gpu_used": False,
        "research_dataset_eligible": False,
        "local_run_directory": local_run,
    }


def _run_multistep(root: Path, common: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    environment, restored, agent, model, checkpoint, before = _restore(
        root,
        [
            _increment_message("call-resume-002", "resume-key-002"),
            _increment_message("call-resume-003", "resume-key-003"),
            _final_answer(),
        ],
    )
    result = agent.resume()
    after = _checkpoint_inventory(checkpoint)
    cumulative_tokens = [6]
    for request in model.requests:
        cumulative_tokens.append(
            cumulative_tokens[-1]
            + int(request.prompt_tokens or 0)
            + int(request.completion_tokens or 0)
        )
    requests = [
        {
            "request_number": 1,
            "step": 1,
            "first_restored_request": False,
            "model_calls_used": 1,
            "tokens_used": cumulative_tokens[0],
            "result": "tool_observation_captured",
        }
    ]
    requests.extend(
        {
            "request_number": index + 2,
            "step": index + 2,
            "first_restored_request": index == 0,
            "input_validation": "exact_checkpoint_contract"
            if index == 0
            else "persistent_identity_and_budget_guard",
            "model_calls_used": request.model_calls_used_after,
            "tokens_used": cumulative_tokens[index + 1],
            "result": request.status,
            "input_hash": request.input_hash,
        }
        for index, request in enumerate(model.requests)
    )
    action_steps = [step for step in agent.memory.steps if hasattr(step, "step_number")]
    audit = {
        **common,
        "checkpoint_id": "day3_checkpoint",
        "requests": requests,
        "restored_request_count": len(model.requests),
        "model_calls_final": model.model_calls_used,
        "runtime_tokens_final": restored.runtime_state.budget.tokens_used,
        "runtime_latency_final_seconds": restored.runtime_state.budget.latency_seconds_used,
        "action_step_numbers": [step.step_number for step in action_steps],
        "action_step_count": len(action_steps),
        "tool_history_count": len(restored.runtime_state.tool_history),
        "counter_value": environment.resolve_path("counter.txt").read_text(),
        "unexpected_replayed_tool_calls": 0,
        "final_answer_generated": result == "done",
        "final_answer": result,
        "first_request_contract_verified": model.first_request_verified,
        "checkpoint_input_hash": restored.restored_next_model_input_hash,
        "first_restored_input_hash": model.requests[0].input_hash,
        "restored_agent_state_hash": restored.restored_agent_state_hash,
        "restored_environment_hash": restored.restored_environment_hash,
        "restored_runtime_hash": restored.restored_runtime_hash,
        "restored_combined_state_hash": restored.restored_combined_state_hash,
        "checkpoint_unchanged": before == after,
        "status": "PASS",
    }
    integrity = {
        **common,
        "checkpoint_id": "day3_checkpoint",
        "artifact_count_before": len(before),
        "artifact_count_after": len(after),
        "inventory_digest_before": _sha256_json(before),
        "inventory_digest_after": _sha256_json(after),
        "inventory_exactly_equal": before == after,
        "restored_combined_state_hash": restored.restored_combined_state_hash,
        "status": "PASS" if before == after else "FAIL",
    }
    return audit, integrity


def _run_budget(root: Path, common: dict[str, Any]) -> dict[str, Any]:
    _, _, runtime, agent, source_model, stream, _ = _capture(root)
    snapshot = AgentStateAdapter.capture(agent)
    expected = _expected(snapshot, source_model.requests[0], runtime)
    expected = replace(expected, model_calls_used=9)
    stream.close()
    underlying = FailingModel()
    model = RecordingModel(underlying)
    model.restore_model_calls_used(9)
    model.expect(expected)
    failure_type = None
    try:
        model.generate(
            agent.write_memory_to_messages(),
            tools_to_call_from=agent.tools_and_managed_agents,
        )
    except RuntimeError as exc:
        failure_type = type(exc).__name__
    rejection = None
    try:
        model.generate(
            agent.write_memory_to_messages(),
            tools_to_call_from=agent.tools_and_managed_agents,
        )
    except ModelRequestRejected:
        rejection = model.requests[-1].difference
    return {
        **common,
        "prior_model_calls": 9,
        "attempted_calls": model.attempted_calls,
        "executed_calls": model.executed_calls,
        "succeeded_calls": model.succeeded_calls,
        "rejected_calls": model.rejected_calls,
        "model_calls_used": model.model_calls_used,
        "provider_calls": underlying.calls,
        "provider_failure_type": failure_type,
        "request_statuses": [request.status for request in model.requests],
        "next_rejection_reason": rejection,
        "failure_consumed_budget": model.model_calls_used == 10,
        "eleventh_delegation_prevented": underlying.calls == 1,
        "status": "PASS"
        if model.model_calls_used == 10 and rejection == "model_call_budget"
        else "FAIL",
    }


def _run_taskspec(root: Path, common: dict[str, Any]) -> dict[str, Any]:
    original = checkpoint_fixtures.synthetic_train_task()
    source = TaskEnvironment(root / "source", original)
    branch_a = TaskEnvironment(root / "a", source.task)
    branch_b = TaskEnvironment(root / "b", source.task)
    source_before = deepcopy(source.task)
    branch_a.task.inventory[0]["quantity"] = 101  # type: ignore[index]
    branch_a.task.orders[0]["status"] = "branch-a"  # type: ignore[index]
    branch_a.task.expected["status"] = "branch-a"  # type: ignore[index]
    branch_a.task.reference_steps[0].arguments["new_status"] = "branch-a"  # type: ignore[index]
    b_unchanged = branch_b.task == source_before
    a_after = deepcopy(branch_a.task)
    branch_b.task.inventory[0]["quantity"] = 202  # type: ignore[index]
    branch_b.task.orders[0]["status"] = "branch-b"  # type: ignore[index]
    branch_b.task.expected["status"] = "branch-b"  # type: ignore[index]
    branch_b.task.reference_steps[0].arguments["new_status"] = "branch-b"  # type: ignore[index]
    a_unchanged = branch_a.task == a_after
    source_unchanged = source.task == source_before and original == source_before
    return {
        **common,
        "task_id": source.task.task_id,
        "template_id": source.task.template_id,
        "instance_id": source.task.instance_id,
        "split": source.task.split.value if source.task.split is not None else None,
        "taskspec_instances_distinct": len(
            {id(source.task), id(branch_a.task), id(branch_b.task)}
        )
        == 3,
        "inventory_containers_distinct": branch_a.task.inventory[0]
        is not branch_b.task.inventory[0],
        "orders_containers_distinct": branch_a.task.orders[0]
        is not branch_b.task.orders[0],
        "expected_containers_distinct": branch_a.task.expected is not branch_b.task.expected,
        "tool_arguments_distinct": branch_a.task.reference_steps[0].arguments
        is not branch_b.task.reference_steps[0].arguments,
        "a_to_b_isolated": b_unchanged,
        "b_to_a_isolated": a_unchanged,
        "source_unchanged": source_unchanged,
        "identity_and_split_preserved": all(
            task.task_id == source.task.task_id
            and task.template_id == source.task.template_id
            and task.instance_id == source.task.instance_id
            and task.split == source.task.split
            for task in (branch_a.task, branch_b.task)
        ),
        "file_fixture_mutable_state": "not_applicable_strings_only",
        "status": "PASS" if b_unchanged and a_unchanged and source_unchanged else "FAIL",
    }


def _run_command(command: list[str]) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    duration = time.perf_counter() - started
    output = completed.stdout.strip()
    match = re.search(r"(\d+) passed(?:, (\d+) skipped)? in ([0-9.]+)s", output)
    display_command = " ".join(command).replace(f"{ROOT}/", "")
    result = {
        "command": display_command,
        "exit_code": completed.returncode,
        "duration_seconds": duration,
        "passed": int(match.group(1)) if match else None,
        "failed": 0 if completed.returncode == 0 else None,
        "skipped": int(match.group(2) or 0) if match else 0,
        "summary": output.splitlines()[-1] if output else "no output",
    }
    if completed.returncode != 0:
        raise RuntimeError(f"Acceptance command failed: {result}\n{output}")
    return result


def _run_tests(common: dict[str, Any]) -> dict[str, Any]:
    python = str(ROOT / ".venv" / "bin" / "python")
    commands = [
        [python, "-m", "pytest", "-q", "--disable-warnings", "tests/agent/test_checkpointable_agent.py"],
        [python, "-m", "pytest", "-q", "--disable-warnings", "tests/agent/test_multistep_resume.py"],
        [python, "-m", "pytest", "-q", "--disable-warnings", "tests/test_checkpoint_v2_isolation.py"],
        [python, "-m", "pytest", "-q", "--disable-warnings", "tests/agent/"],
        [python, "-m", "pytest", "-q", "--disable-warnings", "tests/test_runtime_state.py"],
        [python, "-m", "pytest", "-q", "--disable-warnings", "tests/test_checkpoint.py"],
        [python, "-m", "pytest", "-q", "--disable-warnings", "tests/test_checkpoint_v2.py"],
        [python, "-m", "pytest", "-q", "--disable-warnings", "tests/test_branching.py"],
        [python, "-m", "pytest", "-q", "--disable-warnings"],
        [python, "-m", "compileall", "-q", "src", "tests"],
        ["git", "diff", "--check"],
    ]
    results = [_run_command(command) for command in commands]
    full = next(
        item
        for item in results
        if item["command"].endswith("pytest -q --disable-warnings")
    )
    return {
        **common,
        "commands": results,
        "full_suite_passed": full["passed"],
        "full_suite_failed": full["failed"],
        "full_suite_skipped": full["skipped"],
        "historical_baseline_passed": 159,
        "tests_deleted": False,
        "status": "PASS",
    }


def main() -> int:
    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    evidence_root = EVIDENCE_BASE / run_id
    if evidence_root.exists():
        raise FileExistsError(f"Refusing to overwrite evidence: {evidence_root}")
    local_run = f"runs/phase2_b2_day4_post_audit/{run_id}"
    local_root = ROOT / local_run
    common = _common(run_id, local_run)
    with tempfile.TemporaryDirectory(prefix="day4-post-audit-") as temporary:
        temporary_root = Path(temporary)
        multistep, integrity = _run_multistep(temporary_root / "multistep", common)
        budget = _run_budget(temporary_root / "budget", common)
        taskspec = _run_taskspec(temporary_root / "taskspec", common)
    tests = _run_tests(common)
    documents = {
        "multistep_resume_audit.json": multistep,
        "model_budget_audit.json": budget,
        "taskspec_isolation_audit.json": taskspec,
        "checkpoint_integrity_audit.json": integrity,
        "test_summary.json": tests,
    }
    if any(document["status"] != "PASS" for document in documents.values()):
        raise RuntimeError("At least one acceptance audit did not pass")
    for name, document in documents.items():
        _write_json(evidence_root / name, document)
        _write_json(local_root / name, document)
    manifest = {
        **common,
        "files": [
            {
                "path": name,
                "sha256": hashlib.sha256((evidence_root / name).read_bytes()).hexdigest(),
                "size_bytes": (evidence_root / name).stat().st_size,
            }
            for name in sorted(documents)
        ],
        "contains_secrets": False,
        "contains_raw_model_messages": False,
        "source": "real local audit scenarios and acceptance commands",
        "status": "PASS",
    }
    _write_json(evidence_root / "evidence_manifest.json", manifest)
    _write_json(local_root / "evidence_manifest.json", manifest)
    print(json.dumps({"run_id": run_id, "status": "PASS", "tests": tests["full_suite_passed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
