from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from risk_agent.agent.agent_factory import ModelErrorCategory
from risk_agent.benchmark import (
    TaskEnvironment,
    generate_task_instances,
    load_task_templates,
)
from risk_agent.collection import (
    classify_task_status,
    create_local_domain_tools,
    log_completeness,
    is_retryable_pretool_infra,
    select_initial_tasks,
)
from risk_agent.monitoring import BudgetState, ExecutionMonitor, JsonlTraceWriter, RuntimeState


ROOT = Path(__file__).parents[1]
TEMPLATES = ROOT / "configs" / "task_templates.yaml"


def test_day8_selection_is_train_only_unique_and_balanced() -> None:
    tasks = generate_task_instances(load_task_templates(TEMPLATES))
    selected = select_initial_tasks(tasks)
    assert len(selected) == 24
    assert len({task.task_id for task in selected}) == 24
    assert all(task.split is not None and task.split.value == "train" for task in selected)
    assert all(task.instance_id.endswith("_001") for task in selected)
    assert Counter(task.domain.value for task in selected) == {
        "data_analysis": 6,
        "file_retrieval": 6,
        "api_workflow": 6,
        "calculation_logic": 6,
    }


def test_local_smolagent_tool_adapter_executes_and_records_in_workspace(tmp_path) -> None:
    task = select_initial_tasks(generate_task_instances(load_task_templates(TEMPLATES)))[0]
    environment = TaskEnvironment(tmp_path / "environments", task)
    environment.reset()
    runtime = RuntimeState(
        seed=task.seed,
        budget=BudgetState(max_tokens=100, max_tool_calls=10, max_latency_seconds=10),
    )
    trace = tmp_path / "tools.jsonl"
    monitor = ExecutionMonitor(
        writer=JsonlTraceWriter(trace),
        runtime_state=runtime,
        experiment_id="day8_test",
        run_id="day8_test_run",
        task_id=task.task_id,
        template_id=task.template_id,
        instance_id=task.instance_id,
        model_id="mock",
        model_version="mock",
        prompt_version="test",
        tool_version="test",
        worker_id="worker-0",
        environment_id="local-test",
        additional_context={"execution_location": "local_wsl"},
    )
    tools = {tool.name: tool for tool in create_local_domain_tools(environment=environment, monitor=monitor)}
    step = task.reference_steps[-1]
    result = tools[step.tool](**dict(step.arguments))
    assert result == task.expected["result"]
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool_name"] == step.tool
    assert records[0]["tool_arguments"] == dict(step.arguments)
    assert records[0]["tool_result"] == task.expected["result"]
    assert records[0]["execution_location"] == "local_wsl"


def test_failure_origin_and_completeness_are_explicit() -> None:
    assert classify_task_status(
        validation_passed=False,
        error_category=ModelErrorCategory.TIMEOUT,
        tool_call_count=0,
    ) == ("infra_failure", "model_service")
    assert classify_task_status(
        validation_passed=False,
        error_category=None,
        tool_call_count=1,
    ) == ("task_failure", "agent_or_tool")
    assert classify_task_status(
        validation_passed=True,
        error_category=None,
        tool_call_count=1,
    ) == ("success", "none")
    assert log_completeness([{"a": 1, "b": 2}], ("a", "b")) == 1.0
    assert log_completeness([{"a": 1, "b": None}], ("a", "b")) == 0.5
    assert is_retryable_pretool_infra(
        {"task_status": "infra_failure", "tool_call_count": 0}
    )
    assert not is_retryable_pretool_infra(
        {"task_status": "infra_failure", "tool_call_count": 1}
    )
    assert not is_retryable_pretool_infra(
        {"task_status": "task_failure", "tool_call_count": 0}
    )
