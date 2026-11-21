from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from smolagents import Model, ToolCallingAgent
from smolagents.memory import ActionStep, TaskStep, ToolCall
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
)
from smolagents.monitoring import Timing, TokenUsage

from risk_agent.agent import AgentStateAdapter
from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.benchmark import TaskEnvironment
from risk_agent.benchmark.schema import DataSplit, Domain, TaskSpec, ToolStep
from risk_agent.checkpoint import (
    AdapterStateMetadata,
    AdapterStateStatus,
    CaptureInvariants,
    CheckpointIdentityV2,
    CheckpointIntegrityError,
    CheckpointManager,
    CheckpointManifest,
    CheckpointManifestV2,
    CheckpointSchemaError,
    CheckpointStateMismatchError,
    EnvironmentCheckpointMetadata,
    RuntimeCheckpointMetadata,
    parse_checkpoint_manifest,
)
from risk_agent.monitoring import BudgetState, RuntimeState, ToolHistoryEntry
from risk_agent.tools.calculation_tools import CalculatorTool


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class StubModel(Model):
    def __init__(self, model_id: str = "qwen3-8b-test") -> None:
        super().__init__(
            model_id=model_id,
            seed=20261120,
            temperature=0.0,
            max_tokens=256,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )

    def generate(self, *args, **kwargs):  # pragma: no cover - Day 2 never calls a model
        raise AssertionError("CheckpointManager v2 must not call the model")


def synthetic_train_task() -> TaskSpec:
    return TaskSpec(
        task_id="api_checkpoint_v2",
        template_id="api_template_v2",
        instance_id="api_instance_v2",
        domain=Domain.API_WORKFLOW,
        seed=20261120,
        prompt="Ship order ord-200 exactly once.",
        files=(),
        inventory=({"sku": "sku-200", "quantity": 5},),
        orders=({"order_id": "ord-200", "status": "processing", "version": 1},),
        reference_steps=(
            ToolStep(
                tool="update_order",
                arguments={
                    "order_id": "ord-200",
                    "expected_status": "processing",
                    "new_status": "shipped",
                    "idempotency_key": "v2-test-key",
                },
            ),
        ),
        expected={"order_id": "ord-200", "status": "shipped"},
        dataset_role="day7_catalog",
        split=DataSplit.TRAIN,
    )


def make_agent_snapshot(
    *,
    observation: str | None = "order shipped",
    step_number: int = 1,
    max_steps: int = 10,
):
    agent = ToolCallingAgent(
        tools=[CalculatorTool()],
        model=StubModel(),
        max_steps=max_steps,
        verbosity_level=0,
        name="v2_checkpoint_agent",
        description="Synthetic local Day 2 fixture",
    )
    task = synthetic_train_task().prompt
    tool_call = ChatMessageToolCall(
        id="call-v2-001",
        type="function",
        function=ChatMessageToolCallFunction(
            name="calculator",
            arguments={"expression": "40 + 2", "optional": None, "count": 0},
        ),
    )
    agent.task = task
    agent.step_number = step_number
    agent.state = {"cursor": 0, "optional": None}
    agent.memory.steps = [
        TaskStep(task=task),
        ActionStep(
            step_number=step_number,
            timing=Timing(start_time=0.0, end_time=0.25),
            model_input_messages=[ChatMessage(role=MessageRole.USER, content=task)],
            tool_calls=[
                ToolCall(
                    name="calculator",
                    arguments={"expression": "40 + 2", "optional": None, "count": 0},
                    id="call-v2-001",
                )
            ],
            model_output_message=ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Calling calculator",
                tool_calls=[tool_call],
                token_usage=TokenUsage(input_tokens=0, output_tokens=9),
            ),
            model_output="Calling calculator",
            observations=observation,
            action_output={"value": 42},
            token_usage=TokenUsage(input_tokens=0, output_tokens=9),
            is_final_answer=False,
        ),
    ]
    return AgentStateAdapter.capture(agent)


def make_runtime(
    task: TaskSpec,
    *,
    tokens_used: int = 9,
    tool_calls_used: int = 1,
    latency_seconds_used: float = 0.25,
) -> RuntimeState:
    history = ToolHistoryEntry(
        step_id="step_001",
        tool_name="calculator",
        tool_arguments={"expression": "40 + 2"},
        tool_result="42",
        error_type=None,
        timestamp="2026-11-20T08:00:00+00:00",
        token_usage={"input_tokens": 0, "output_tokens": 9},
        latency_seconds=0.25,
        worker_id="worker-0",
        environment_id="synthetic-v2-environment",
        checkpoint_id=None,
    )
    return RuntimeState(
        seed=task.seed,
        agent_memory=[{"role": "tool", "content": "order shipped"}],
        tool_history=[history],
        tool_state={"cursor": 1, "fault_state": {"calls": 0}},
        budget=BudgetState(
            max_tokens=50_000,
            max_tool_calls=12,
            max_latency_seconds=900.0,
            tokens_used=tokens_used,
            tool_calls_used=tool_calls_used,
            latency_seconds_used=latency_seconds_used,
        ),
    )


def identity() -> CheckpointIdentityV2:
    return CheckpointIdentityV2(
        experiment_id="experiment_v2_test",
        run_id="run_v2_test",
        task_run_id="task_run_v2_test",
        model_version="qwen3-8b-test-revision",
        worker_id="worker-0",
        prompt_version="prompt-v2-test",
        tool_version="tools-v2-test",
        verifier_version="verifier-v2-test",
        verifier_content_hash=digest("verifier"),
        catalog_hash=digest("catalog"),
        split_manifest_hash=digest("split"),
        controller_type="qwen3_model",
        execution_mode="model_backed_smoke",
        created_at="2026-11-20T08:00:00+00:00",
    )


def runtime_metadata(
    *,
    missing_tokens: bool = False,
    model_calls_used: int = 1,
) -> RuntimeCheckpointMetadata:
    return RuntimeCheckpointMetadata(
        max_model_calls=10,
        model_calls_used=model_calls_used,
        prompt_tokens_used=None if missing_tokens else 0,
        completion_tokens_used=None if missing_tokens else 9,
        resource_usage={"wall_seconds": 0.25, "gpu_used": False},
        tool_adapter_state=AdapterStateMetadata(
            status=AdapterStateStatus.STATEFUL,
            state={"next_call_index": 2},
        ),
        fault_adapter_state=AdapterStateMetadata(
            status=AdapterStateStatus.EMPTY,
            state={},
        ),
        phase="after_tool_observation_before_model_generation",
        next_operation="model_generation",
    )


def environment_metadata(*, include_latest_operation: bool = True) -> EnvironmentCheckpointMetadata:
    return EnvironmentCheckpointMetadata(
        completed_side_effects=[
            {
                "kind": "order_update",
                "idempotency_key": "v2-test-key",
                "commit_state": "committed",
            }
        ],
        latest_completed_operation=(
            {"kind": "tool_call", "call_id": "call-v2-001"}
            if include_latest_operation
            else None
        ),
    )


def invariants() -> CaptureInvariants:
    return CaptureInvariants(
        observation_in_memory=True,
        next_model_request_not_started=True,
        non_terminal=True,
        remaining_step_available=True,
        snapshot_synchronous=True,
        verifier_not_run=True,
    )


def prepared_environment(tmp_path: Path) -> TaskEnvironment:
    task = synthetic_train_task()
    environment = TaskEnvironment(tmp_path / "environments", task)
    environment.reset()
    note = environment.resolve_path("notes/progress.txt", must_exist=False)
    note.parent.mkdir(parents=True)
    note.write_text("captured\n", encoding="utf-8")
    with environment.database() as connection:
        connection.execute(
            "INSERT INTO idempotency_keys VALUES (?, ?, ?)",
            ("v2-test-key", digest("request"), '{"status":"shipped"}'),
        )
        connection.execute("UPDATE orders SET status='shipped', version=2 WHERE order_id='ord-200'")
        connection.execute(
            "CREATE TABLE audit_events (group_id TEXT, sequence INTEGER, event TEXT, "
            "PRIMARY KEY(group_id, sequence))"
        )
        connection.executemany(
            "INSERT INTO audit_events VALUES (?, ?, ?)",
            [("order", 2, "committed"), ("order", 1, "started")],
        )
        connection.commit()
    return environment


def create_checkpoint(
    tmp_path: Path,
    *,
    missing_tokens: bool = False,
    include_latest_operation: bool = True,
):
    environment = prepared_environment(tmp_path)
    manager = CheckpointManager(tmp_path / "checkpoints")
    snapshot = manager.snapshot_v2(
        checkpoint_id="checkpoint_v2_001",
        environment=environment,
        runtime_state=make_runtime(environment.task),
        agent_state_snapshot=make_agent_snapshot(),
        identity=identity(),
        runtime_metadata=runtime_metadata(missing_tokens=missing_tokens),
        environment_metadata=environment_metadata(
            include_latest_operation=include_latest_operation
        ),
        capture_invariants=invariants(),
    )
    return manager, environment, snapshot


def test_manifest_v1_v2_round_trip_and_dispatch(tmp_path: Path) -> None:
    v1_raw = {
        "schema_version": 1,
        "checkpoint_id": "checkpoint_001",
        "created_at": "2026-11-20T00:00:00+00:00",
        "task_id": "task_001",
        "state_hash": digest("state"),
        "environment_hash": digest("environment"),
        "runtime_hash": digest("runtime"),
        "task_config_hash": digest("task"),
        "mock_api_state_hash": digest("api"),
        "artifact_checksums": {"runtime_state.json": digest("runtime artifact")},
    }
    parsed_v1 = parse_checkpoint_manifest(v1_raw)
    assert isinstance(parsed_v1, CheckpointManifest)
    assert parsed_v1.to_dict() == v1_raw

    _, _, snapshot = create_checkpoint(tmp_path)
    raw = json.loads((snapshot.checkpoint_path / "manifest.json").read_text())
    parsed = parse_checkpoint_manifest(raw)
    assert isinstance(parsed, CheckpointManifestV2)
    assert parsed.to_dict() == raw
    with pytest.raises(CheckpointSchemaError, match="Unsupported"):
        parse_checkpoint_manifest({"schema_version": "3.0.0"})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw.pop("hashes"),
        lambda raw: raw.__setitem__("extra", True),
        lambda raw: raw["hashes"].__setitem__("agent_state_hash", "not-a-hash"),
        lambda raw: raw["identity"].__setitem__("split", "development"),
        lambda raw: raw["agent_context"].__setitem__("agent_class", "OtherAgent"),
    ],
)
def test_manifest_v2_strictly_rejects_missing_extra_and_invalid_fields(
    tmp_path: Path, mutation
) -> None:
    _, _, snapshot = create_checkpoint(tmp_path)
    raw = snapshot.manifest.to_dict()
    mutation(raw)
    with pytest.raises(CheckpointSchemaError):
        CheckpointManifestV2.from_dict(raw)


def test_snapshot_verify_and_restore_complete_v2_state(tmp_path: Path) -> None:
    manager, environment, snapshot = create_checkpoint(tmp_path)
    root = snapshot.checkpoint_path
    assert snapshot.manifest.schema_version == "2.0.0"
    assert len(snapshot.manifest.artifacts) >= 10
    assert (root / "agent/agent_state.json").is_file()
    assert (root / "environment/sqlite_logical_dump.json").is_file()
    assert (root / "runtime/fault_adapter_state.json").is_file()
    assert (root / "identity/identity_config.json").is_file()
    manifest_text = (root / "manifest.json").read_text()
    assert str(tmp_path) not in manifest_text
    assert snapshot.manifest.environment_state["pending_operation"] == {
        "kind": "model_generation",
        "next_step_number": 2,
    }
    calls = json.loads((root / "agent/tool_calls.json").read_text())
    observations = json.loads((root / "agent/tool_observations.json").read_text())
    assert calls[-1]["executed_tool_calls"][0]["id"] == "call-v2-001"
    assert calls[-1]["executed_tool_calls"][0]["arguments"]["count"] == 0
    assert calls[-1]["executed_tool_calls"][0]["arguments"]["optional"] is None
    assert observations[-1]["call_ids"] == ["call-v2-001"]
    assert observations[-1]["observation"] == "order shipped"
    hashes = snapshot.manifest.hashes
    assert hashes["combined_state_hash"] == canonical_hash(
        {
            "schema_version": "2.0.0",
            "agent_state_hash": hashes["agent_state_hash"],
            "next_model_input_hash": hashes["next_model_input_hash"],
            "environment_state_hash": hashes["environment_state_hash"],
            "runtime_state_hash": hashes["runtime_state_hash"],
            "identity_config_hash": hashes["identity_config_hash"],
        }
    )
    logical = json.loads((root / "environment/sqlite_logical_dump.json").read_text())
    audit = next(item for item in logical["tables"] if item["name"] == "audit_events")
    assert audit["primary_key"] == ["group_id", "sequence"]
    assert [row["sequence"] for row in audit["rows"]] == [1, 2]

    verified = manager.verify_v2(checkpoint_id="checkpoint_v2_001", environment=environment)
    assert verified.agent_state_snapshot.agent_state_hash == snapshot.manifest.hashes["agent_state_hash"]
    assert verified.agent_state_snapshot.payload["next_model_input_hash"] == (
        snapshot.manifest.hashes["next_model_input_hash"]
    )
    with pytest.raises(CheckpointIntegrityError, match="restore_v2"):
        manager.restore(checkpoint_id="checkpoint_v2_001", environment=environment)

    environment.resolve_path("notes/progress.txt").write_text("mutated\n", encoding="utf-8")
    environment.resolve_path("notes/extra.txt", must_exist=False).write_text("extra", encoding="utf-8")
    with environment.database() as connection:
        connection.execute("UPDATE orders SET status='cancelled', version=9")
        connection.commit()
    restored = manager.restore_v2(checkpoint_id="checkpoint_v2_001", environment=environment)

    assert environment.resolve_path("notes/progress.txt").read_text() == "captured\n"
    assert not environment.resolve_path("notes/extra.txt", must_exist=False).exists()
    with environment.database() as connection:
        order = connection.execute(
            "SELECT status, version FROM orders WHERE order_id='ord-200'"
        ).fetchone()
        keys = connection.execute("SELECT idempotency_key FROM idempotency_keys").fetchall()
    assert tuple(order) == ("shipped", 2)
    assert [tuple(row) for row in keys] == [("v2-test-key",)]
    assert restored.runtime_state.to_dict() == make_runtime(environment.task).to_dict()
    assert restored.restored_environment_hash == snapshot.manifest.hashes["environment_state_hash"]
    assert restored.restored_runtime_hash == snapshot.manifest.hashes["runtime_state_hash"]
    assert restored.restored_agent_state_hash == snapshot.manifest.hashes["agent_state_hash"]
    assert restored.restored_next_model_input_hash == snapshot.manifest.hashes["next_model_input_hash"]
    assert restored.restored_combined_state_hash == snapshot.manifest.hashes["combined_state_hash"]
    with pytest.raises(FileExistsError):
        manager.snapshot_v2(
            checkpoint_id="checkpoint_v2_001",
            environment=environment,
            runtime_state=make_runtime(environment.task),
            agent_state_snapshot=make_agent_snapshot(),
            identity=identity(),
            runtime_metadata=runtime_metadata(),
            environment_metadata=environment_metadata(),
            capture_invariants=invariants(),
        )

def test_runtime_missing_token_fields_remain_null_and_adapter_states_are_distinct(tmp_path: Path) -> None:
    manager, environment, snapshot = create_checkpoint(tmp_path, missing_tokens=True)
    budget = snapshot.manifest.runtime_state["budget_state"]
    assert budget["prompt_tokens_used"] is None
    assert budget["completion_tokens_used"] is None
    verified = manager.verify_v2(checkpoint_id="checkpoint_v2_001", environment=environment)
    root = verified.checkpoint_path
    tool_state = json.loads((root / "runtime/tool_adapter_state.json").read_text())
    fault_state = json.loads((root / "runtime/fault_adapter_state.json").read_text())
    assert tool_state == {"status": "stateful", "state": {"next_call_index": 2}, "reason": None}
    assert fault_state == {"status": "empty", "state": {}, "reason": None}


@pytest.mark.parametrize(
    ("case", "tokens_used", "tool_calls_used", "latency_used", "model_calls", "step", "allowed"),
    [
        ("model_remaining", 9, 1, 0.25, 9, 1, True),
        ("model_exhausted", 9, 1, 0.25, 10, 1, False),
        ("token_remaining", 49_999, 1, 0.25, 1, 1, True),
        ("token_exhausted", 50_000, 1, 0.25, 1, 1, False),
        ("latency_remaining", 9, 1, 899.9, 1, 1, True),
        ("latency_exhausted", 9, 1, 900.0, 1, 1, False),
        ("step_exhausted", 9, 1, 0.25, 1, 10, False),
        ("tool_exhausted_model_allowed", 9, 12, 0.25, 1, 1, True),
    ],
)
def test_snapshot_v2_requires_budget_for_declared_next_model_generation(
    tmp_path: Path,
    case: str,
    tokens_used: int,
    tool_calls_used: int,
    latency_used: float,
    model_calls: int,
    step: int,
    allowed: bool,
) -> None:
    environment = prepared_environment(tmp_path)
    manager = CheckpointManager(tmp_path / "checkpoints")
    before = environment.state_hash()

    def capture():
        return manager.snapshot_v2(
            checkpoint_id=f"checkpoint_{case}",
            environment=environment,
            runtime_state=make_runtime(
                environment.task,
                tokens_used=tokens_used,
                tool_calls_used=tool_calls_used,
                latency_seconds_used=latency_used,
            ),
            agent_state_snapshot=make_agent_snapshot(step_number=step),
            identity=identity(),
            runtime_metadata=runtime_metadata(
                missing_tokens=tokens_used != 9,
                model_calls_used=model_calls,
            ),
            environment_metadata=environment_metadata(),
            capture_invariants=invariants(),
        )

    if allowed:
        result = capture()
        assert result.checkpoint_path.is_dir()
    else:
        with pytest.raises((ValueError, CheckpointStateMismatchError)):
            capture()
        assert not (manager.checkpoint_root / f"checkpoint_{case}").exists()
    assert environment.state_hash() == before


@pytest.mark.parametrize("exhausted_budget", ["model", "tokens", "latency"])
def test_verify_and_restore_reject_internally_consistent_exhausted_budget_before_mutation(
    tmp_path: Path,
    exhausted_budget: str,
) -> None:
    manager, environment, snapshot = create_checkpoint(tmp_path)
    root = snapshot.checkpoint_path
    manifest_path = root / "manifest.json"
    raw = json.loads(manifest_path.read_text())
    runtime_path = root / "runtime/runtime_state.json"
    budget_path = root / "runtime/budget_state.json"
    runtime_document = json.loads(runtime_path.read_text())
    budget = json.loads(budget_path.read_text())
    if exhausted_budget == "model":
        budget["model_calls_used"] = budget["max_model_calls"]
    elif exhausted_budget == "tokens":
        runtime_document["budget"]["tokens_used"] = runtime_document["budget"]["max_tokens"]
    else:
        exhausted = runtime_document["budget"]["max_latency_seconds"]
        runtime_document["budget"]["latency_seconds_used"] = exhausted
        budget["latency_seconds_used"] = exhausted

    runtime_path.write_text(json.dumps(runtime_document, indent=2, sort_keys=True) + "\n")
    budget_path.write_text(json.dumps(budget, indent=2, sort_keys=True) + "\n")
    raw["runtime_state"]["budget_state"] = budget

    def artifact_value(relative: str):
        return json.loads((root / relative).read_text())

    runtime_hash_document = {
        "runtime_state": runtime_document,
        "tool_history": artifact_value("runtime/tool_history.json"),
        "tool_adapter_state": artifact_value("runtime/tool_adapter_state.json"),
        "fault_adapter_state": artifact_value("runtime/fault_adapter_state.json"),
        "budget_state": budget,
        "resource_usage": artifact_value("runtime/resource_usage.json"),
        "execution_position": artifact_value("runtime/execution_position.json"),
    }
    raw["hashes"]["runtime_state_hash"] = canonical_hash(runtime_hash_document)
    raw["hashes"]["combined_state_hash"] = canonical_hash(
        {
            "schema_version": "2.0.0",
            "agent_state_hash": raw["hashes"]["agent_state_hash"],
            "next_model_input_hash": raw["hashes"]["next_model_input_hash"],
            "environment_state_hash": raw["hashes"]["environment_state_hash"],
            "runtime_state_hash": raw["hashes"]["runtime_state_hash"],
            "identity_config_hash": raw["hashes"]["identity_config_hash"],
        }
    )
    for record in raw["artifacts"]:
        path = root / record["path"]
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        record["size_bytes"] = path.stat().st_size
    manifest_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")

    protected = environment.resolve_path("notes/progress.txt")
    protected.write_text("must-survive\n", encoding="utf-8")
    with pytest.raises(CheckpointIntegrityError, match="budget|Token|latency"):
        manager.restore_v2(checkpoint_id="checkpoint_v2_001", environment=environment)
    assert protected.read_text() == "must-survive\n"


def test_prompt_token_missingness_and_real_zero_remain_distinct(tmp_path: Path) -> None:
    _, _, observed = create_checkpoint(tmp_path / "observed")
    _, _, missing = create_checkpoint(tmp_path / "missing", missing_tokens=True)
    observed_budget = observed.manifest.runtime_state["budget_state"]
    missing_budget = missing.manifest.runtime_state["budget_state"]
    assert observed_budget["prompt_tokens_used"] == 0
    assert missing_budget["prompt_tokens_used"] is None
    assert observed_budget["prompt_tokens_used"] is not missing_budget["prompt_tokens_used"]


def test_optional_latest_completed_operation_is_omitted_and_restores(tmp_path: Path) -> None:
    manager, environment, snapshot = create_checkpoint(
        tmp_path,
        include_latest_operation=False,
    )
    assert "latest_completed_operation_ref" not in snapshot.manifest.environment_state
    assert not (snapshot.checkpoint_path / "environment/latest_completed_operation.json").exists()
    verified = manager.verify_v2(checkpoint_id="checkpoint_v2_001", environment=environment)
    assert verified.manifest.hashes["environment_state_hash"] == (
        snapshot.manifest.hashes["environment_state_hash"]
    )
    environment.resolve_path("notes/progress.txt").write_text("mutated\n", encoding="utf-8")
    restored = manager.restore_v2(checkpoint_id="checkpoint_v2_001", environment=environment)
    assert environment.resolve_path("notes/progress.txt").read_text() == "captured\n"
    assert restored.restored_environment_hash == snapshot.manifest.hashes["environment_state_hash"]


@pytest.mark.parametrize("mutation", ["missing", "tampered"])
def test_present_latest_completed_operation_still_requires_intact_artifact(
    tmp_path: Path,
    mutation: str,
) -> None:
    manager, environment, snapshot = create_checkpoint(tmp_path)
    latest = snapshot.checkpoint_path / "environment/latest_completed_operation.json"
    protected = environment.resolve_path("notes/progress.txt")
    protected.write_text("must-survive\n", encoding="utf-8")
    if mutation == "missing":
        latest.unlink()
    else:
        latest.write_text('{"kind":"tampered"}\n', encoding="utf-8")
    with pytest.raises(CheckpointIntegrityError):
        manager.restore_v2(checkpoint_id="checkpoint_v2_001", environment=environment)
    assert protected.read_text() == "must-survive\n"


def test_all_four_adapter_state_availability_modes_are_distinct() -> None:
    stateful = AdapterStateMetadata(AdapterStateStatus.STATEFUL, {"calls": 1})
    empty = AdapterStateMetadata(AdapterStateStatus.EMPTY, {})
    absent = AdapterStateMetadata(AdapterStateStatus.NOT_INSTANTIATED, None)
    unavailable = AdapterStateMetadata(
        AdapterStateStatus.UNAVAILABLE,
        None,
        reason="adapter has no export API",
    )
    assert {item.to_dict()["status"] for item in (stateful, empty, absent, unavailable)} == {
        "stateful",
        "empty",
        "not_instantiated",
        "unavailable",
    }
    with pytest.raises(ValueError):
        AdapterStateMetadata(AdapterStateStatus.UNAVAILABLE, None)


@pytest.mark.parametrize(
    "case",
    ["delete", "missing_agent", "modify", "unregistered", "symlink", "manifest", "traversal"],
)
def test_integrity_failures_are_detected_before_workspace_mutation(tmp_path: Path, case: str) -> None:
    manager, environment, snapshot = create_checkpoint(tmp_path)
    root = snapshot.checkpoint_path
    protected = environment.resolve_path("notes/progress.txt")
    protected.write_text("must-survive\n", encoding="utf-8")
    artifact = root / "runtime/resource_usage.json"
    if case == "delete":
        artifact.unlink()
    elif case == "missing_agent":
        (root / "agent/agent_state.json").unlink()
    elif case == "modify":
        artifact.write_text("{}\n", encoding="utf-8")
    elif case == "unregistered":
        (root / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    elif case == "symlink":
        artifact.unlink()
        artifact.symlink_to(tmp_path / "outside.json")
    else:
        manifest_path = root / "manifest.json"
        raw = json.loads(manifest_path.read_text())
        if case == "manifest":
            raw["hashes"]["combined_state_hash"] = digest("forged")
        else:
            raw["artifacts"][0]["path"] = "../escape.json"
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CheckpointIntegrityError):
        manager.restore_v2(checkpoint_id="checkpoint_v2_001", environment=environment)
    assert protected.read_text() == "must-survive\n"


def test_sqlite_corruption_and_missing_tool_observation_are_rejected_atomically(tmp_path: Path) -> None:
    manager, environment, snapshot = create_checkpoint(tmp_path)
    protected = environment.resolve_path("notes/progress.txt")
    protected.write_text("unchanged\n", encoding="utf-8")
    sqlite_backup = snapshot.checkpoint_path / "environment/sqlite_backup.sqlite3"
    sqlite_backup.write_bytes(b"not sqlite")
    with pytest.raises(CheckpointIntegrityError):
        manager.restore_v2(checkpoint_id="checkpoint_v2_001", environment=environment)
    assert protected.read_text() == "unchanged\n"

    other_root = tmp_path / "other-checkpoints"
    other_manager = CheckpointManager(other_root)
    with pytest.raises(ValueError, match="Observation"):
        other_manager.snapshot_v2(
            checkpoint_id="checkpoint_v2_002",
            environment=environment,
            runtime_state=make_runtime(environment.task),
            agent_state_snapshot=make_agent_snapshot(observation=None),
            identity=identity(),
            runtime_metadata=runtime_metadata(),
            environment_metadata=environment_metadata(),
            capture_invariants=invariants(),
        )
    assert not (other_root / "checkpoint_v2_002").exists()


def test_v2_rejects_smoke_task_without_formal_split(tmp_path: Path) -> None:
    task = synthetic_train_task()
    smoke_task = TaskSpec(
        task_id=task.task_id,
        template_id=task.template_id,
        instance_id=task.instance_id,
        domain=task.domain,
        seed=task.seed,
        prompt=task.prompt,
        files=task.files,
        inventory=task.inventory,
        orders=task.orders,
        reference_steps=task.reference_steps,
        expected=task.expected,
        dataset_role="day3_smoke",
        split=None,
    )
    environment = TaskEnvironment(tmp_path / "smoke", smoke_task)
    environment.reset()
    manager = CheckpointManager(tmp_path / "checkpoints")
    with pytest.raises(Exception, match="split"):
        manager.snapshot_v2(
            checkpoint_id="checkpoint_v2_001",
            environment=environment,
            runtime_state=make_runtime(smoke_task),
            agent_state_snapshot=make_agent_snapshot(),
            identity=identity(),
            runtime_metadata=runtime_metadata(),
            environment_metadata=environment_metadata(),
            capture_invariants=invariants(),
        )
