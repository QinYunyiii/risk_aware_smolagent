from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from smolagents import Model, Tool
from smolagents.agents import AgentParsingError
from smolagents.memory import ActionStep
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
)
from smolagents.monitoring import Timing, TokenUsage

from risk_agent.agent import (
    AGENT_STATE_SCHEMA_VERSION,
    AgentStateAdapter,
    CheckpointableToolCallingAgent,
    RecordingModel,
)
from risk_agent.monitoring import (
    AuditPersistenceError,
    BudgetState,
    ProviderAuditContext,
    ProviderResponseAudit,
    RuntimeState,
)


class ScriptedProviderModel(Model):
    def __init__(
        self,
        responses: list[ChatMessage] | None = None,
        *,
        exception: BaseException | None = None,
    ) -> None:
        super().__init__(
            model_id="Qwen/Qwen3-8B",
            seed=20261118,
            temperature=0.0,
            max_tokens=256,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        self.responses = list(responses or [])
        self.exception = exception
        self.calls = 0
        self.model_version = "Qwen3-8B-modelscope-snapshot"
        self.worker_id = "worker-0"
        self.endpoint = "https://worker.test/v1"

    def generate(self, *args: Any, **kwargs: Any) -> ChatMessage:
        self.calls += 1
        if self.exception is not None:
            raise self.exception
        if not self.responses:
            raise AssertionError("Unexpected scripted provider call")
        return self.responses.pop(0)


class CountingTool(Tool):
    name = "echo_value"
    description = "Return one value."
    inputs = {"value": {"type": "string", "description": "Value to return."}}
    output_type = "string"

    def __init__(self) -> None:
        self.calls = 0
        super().__init__()

    def forward(self, value: str) -> str:
        self.calls += 1
        return value


class StatusError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"provider returned HTTP {status_code}")


class FailingWriter:
    def append(self, record: Any) -> None:
        raise PermissionError("synthetic audit directory is read-only")


def _context(worker_id: str = "worker-0") -> ProviderAuditContext:
    return ProviderAuditContext(
        run_id="audit_test_run",
        task_id="audit_test_task",
        attempt_index=1,
        worker_id=worker_id,
        model_id="Qwen/Qwen3-8B",
        model_version="Qwen3-8B-modelscope-snapshot",
        endpoint=f"https://fixture:secret@{worker_id}.test/v1?api_key=hidden",
        phase="source",
    )


def _raw(
    *,
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str | None = "tool_calls",
    usage: dict[str, Any] | None = None,
    response_id: str = "chatcmpl-audit-001",
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": 1_800_000_000,
        "model": "Qwen/Qwen3-8B",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }


def _structured_message() -> ChatMessage:
    calls = [
        ChatMessageToolCall(
            id="call-audit-001",
            type="function",
            function=ChatMessageToolCallFunction(
                name="echo_value", arguments={"value": "ok"}
            ),
        )
    ]
    raw_calls = [
        {
            "id": "call-audit-001",
            "type": "function",
            "function": {
                "name": "echo_value",
                "arguments": '{"value":"ok"}',
            },
        }
    ]
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content="",
        tool_calls=calls,
        raw=_raw(
            tool_calls=raw_calls,
            usage={"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        ),
        token_usage=TokenUsage(input_tokens=12, output_tokens=4),
    )


def _text_message(content: str, *, finish_reason: str = "stop") -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=content,
        tool_calls=[],
        raw=_raw(
            content=content,
            tool_calls=[],
            finish_reason=finish_reason,
            usage={"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        ),
        token_usage=TokenUsage(input_tokens=20, output_tokens=8),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _recording(
    tmp_path: Path,
    model: ScriptedProviderModel,
    *,
    audit: ProviderResponseAudit | None = None,
    runtime: RuntimeState | None = None,
) -> tuple[RecordingModel, ProviderResponseAudit]:
    provider_audit = audit or ProviderResponseAudit(tmp_path / "audit")
    return (
        RecordingModel(
            model,
            runtime_state=runtime,
            max_model_calls=10,
            provider_audit=provider_audit,
            provider_audit_context=_context(),
        ),
        provider_audit,
    )


def _agent(recording: RecordingModel, tool: CountingTool) -> CheckpointableToolCallingAgent:
    agent = CheckpointableToolCallingAgent(
        tools=[tool],
        model=recording,
        max_steps=3,
        verbosity_level=0,
        name="audit_test_agent",
        description="Synthetic provider audit test Agent",
    )
    agent.task = "Use the echo tool once."
    # ``_run_stream`` establishes this before delegating the first ActionStep. These
    # focused tests call the pinned step boundary directly to inspect the original
    # exception, so reproduce that lifecycle state explicitly.
    agent.step_number = 1
    return agent


def _one_step(agent: CheckpointableToolCallingAgent) -> list[Any]:
    step = ActionStep(step_number=1, timing=Timing(start_time=1.0))
    return list(agent._step_stream(step))


def test_structured_tool_call_persists_sdk_snapshot_usage_finish_reason_and_hash(
    tmp_path: Path,
) -> None:
    recording, audit = _recording(tmp_path, ScriptedProviderModel([_structured_message()]))
    tool = CountingTool()

    _one_step(_agent(recording, tool))

    response = _read_jsonl(audit.response_path)[0]
    parsing = _read_jsonl(audit.parsing_path)[0]
    snapshot = response["raw_response_snapshot"]
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert response["raw_response_snapshot_kind"] == "sdk_visible_provider_response"
    assert response["finish_reason"] == "tool_calls"
    assert response["prompt_tokens"] == 12
    assert response["completion_tokens"] == 4
    assert response["total_tokens"] == 16
    assert response["usage_consistency"]["status"] == "match"
    assert snapshot["choices"][0]["message"]["tool_calls"][0]["id"] == "call-audit-001"
    assert snapshot["choices"][0]["message"]["tool_calls"][0]["function"] == {
        "name": "echo_value",
        "arguments": '{"value":"ok"}',
    }
    assert response["raw_response_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert response["endpoint"] == "https://worker-0.test/v1"
    assert parsing["audit_request_id"] == response["audit_request_id"]
    assert parsing["parsing_status"] == "success"
    assert tool.calls == 1


def test_text_tool_call_preserves_original_content_and_correlates_success(
    tmp_path: Path,
) -> None:
    content = '<tool_call>\n{"name":"echo_value","arguments":{"value":"text"}}\n</tool_call>'
    recording, audit = _recording(tmp_path, ScriptedProviderModel([_text_message(content)]))
    tool = CountingTool()

    _one_step(_agent(recording, tool))

    response = _read_jsonl(audit.response_path)[0]
    parsing = _read_jsonl(audit.parsing_path)[0]
    assert response["raw_response_snapshot"]["choices"][0]["message"]["content"] == content
    assert response["raw_response_snapshot"]["choices"][0]["message"]["tool_calls"] == []
    assert parsing["audit_request_id"] == response["audit_request_id"]
    assert parsing["parsing_status"] == "success"
    assert tool.calls == 1


def test_malformed_text_json_is_persisted_before_original_parsing_error(
    tmp_path: Path,
) -> None:
    content = '{"name":"echo_value","arguments":{"value":"broken"'
    recording, audit = _recording(tmp_path, ScriptedProviderModel([_text_message(content)]))
    tool = CountingTool()
    step = ActionStep(step_number=1, timing=Timing(start_time=1.0))

    with pytest.raises(AgentParsingError):
        list(_agent(recording, tool)._step_stream(step))

    response = _read_jsonl(audit.response_path)[0]
    parsing = _read_jsonl(audit.parsing_path)[0]
    assert response["raw_response_snapshot"]["choices"][0]["message"]["content"] == content
    assert parsing["audit_request_id"] == response["audit_request_id"]
    assert parsing["parsing_status"] == "failed"
    assert parsing["error_category"] == "tool_call_parse_failure"
    assert parsing["exception_type"] == "AgentParsingError"
    assert tool.calls == 0
    assert recording.executed_calls == 1


def test_synthetic_256_token_length_boundary_is_not_transport_failure(tmp_path: Path) -> None:
    content = '{"name":"echo_value","arguments":{"value":"truncated"'
    message = ChatMessage(
        role=MessageRole.ASSISTANT,
        content=content,
        tool_calls=[],
        raw=_raw(
            content=content,
            tool_calls=[],
            finish_reason="length",
            usage={"prompt_tokens": 80, "completion_tokens": 256, "total_tokens": 336},
        ),
        token_usage=TokenUsage(input_tokens=80, output_tokens=256),
    )
    recording, audit = _recording(tmp_path, ScriptedProviderModel([message]))

    with pytest.raises(AgentParsingError):
        _one_step(_agent(recording, CountingTool()))

    response = _read_jsonl(audit.response_path)[0]
    parsing = _read_jsonl(audit.parsing_path)[0]
    assert response["request_status"] == "response_received"
    assert response["error_category"] is None
    assert response["finish_reason"] == "length"
    assert response["completion_tokens"] == 256
    assert response["delegated_completion_token_limit"] == 256
    assert parsing["error_category"] == "tool_call_parse_failure"


def test_missing_usage_remains_null_and_incomplete(tmp_path: Path) -> None:
    message = ChatMessage(
        role=MessageRole.ASSISTANT,
        content="done",
        tool_calls=[],
        raw=_raw(content="done", tool_calls=[], usage=None),
        token_usage=None,
    )
    recording, audit = _recording(tmp_path, ScriptedProviderModel([message]))

    recording.generate([])

    response = _read_jsonl(audit.response_path)[0]
    assert response["prompt_tokens"] is None
    assert response["completion_tokens"] is None
    assert response["total_tokens"] is None
    assert response["usage_completeness"] == "incomplete"
    assert recording.requests[0].prompt_tokens is None
    assert recording.requests[0].completion_tokens is None


def test_partial_usage_does_not_infer_missing_completion_or_total(tmp_path: Path) -> None:
    message = ChatMessage(
        role=MessageRole.ASSISTANT,
        content="done",
        tool_calls=[],
        raw=_raw(content="done", tool_calls=[], usage={"prompt_tokens": 19}),
        token_usage=None,
    )
    recording, audit = _recording(tmp_path, ScriptedProviderModel([message]))

    recording.generate([])

    response = _read_jsonl(audit.response_path)[0]
    assert response["prompt_tokens"] == 19
    assert response["completion_tokens"] is None
    assert response["total_tokens"] is None
    assert response["usage_completeness"] == "incomplete"


def test_provider_and_chat_message_usage_mismatch_is_preserved(tmp_path: Path) -> None:
    message = ChatMessage(
        role=MessageRole.ASSISTANT,
        content="done",
        tool_calls=[],
        raw=_raw(
            content="done",
            tool_calls=[],
            usage={"prompt_tokens": 19, "completion_tokens": 3, "total_tokens": 22},
        ),
        token_usage=TokenUsage(input_tokens=18, output_tokens=4),
    )
    recording, audit = _recording(tmp_path, ScriptedProviderModel([message]))

    recording.generate([])

    response = _read_jsonl(audit.response_path)[0]
    assert response["provider_usage"] == {
        "prompt_tokens": 19,
        "completion_tokens": 3,
        "total_tokens": 22,
    }
    assert response["chat_message_usage"] == {
        "prompt_tokens": 18,
        "completion_tokens": 4,
        "total_tokens": None,
    }
    assert response["usage_consistency"]["status"] == "mismatch"
    assert set(response["usage_consistency"]["mismatches"]) == {
        "prompt_tokens",
        "completion_tokens",
    }


@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (StatusError(429), "provider_error"),
        (StatusError(503), "provider_error"),
        (TimeoutError("synthetic timeout"), "transport_failure"),
        (ConnectionError("synthetic connection failure"), "transport_failure"),
    ],
)
def test_provider_failures_are_audited_once_without_usage_or_retry(
    tmp_path: Path, exception: BaseException, category: str
) -> None:
    underlying = ScriptedProviderModel(exception=exception)
    recording, audit = _recording(tmp_path, underlying)

    with pytest.raises(type(exception)):
        recording.generate([])

    response = _read_jsonl(audit.response_path)[0]
    assert response["request_status"] == category
    assert response["error_category"] == category
    assert response["prompt_tokens"] is None
    assert response["completion_tokens"] is None
    assert response["total_tokens"] is None
    assert response["finish_reason"] is None
    assert response["latency_seconds"] >= 0
    assert underlying.calls == 1
    assert recording.executed_calls == 1


def test_missing_finish_reason_stays_null(tmp_path: Path) -> None:
    message = ChatMessage(
        role=MessageRole.ASSISTANT,
        content="done",
        tool_calls=[],
        raw=_raw(
            content="done",
            tool_calls=[],
            finish_reason=None,
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        ),
        token_usage=TokenUsage(input_tokens=2, output_tokens=1),
    )
    recording, audit = _recording(tmp_path, ScriptedProviderModel([message]))

    recording.generate([])

    assert _read_jsonl(audit.response_path)[0]["finish_reason"] is None


def test_audit_persistence_failure_stops_before_tool_execution(tmp_path: Path) -> None:
    audit = ProviderResponseAudit(
        tmp_path / "audit",
        response_writer=FailingWriter(),
    )
    recording, _ = _recording(
        tmp_path, ScriptedProviderModel([_structured_message()]), audit=audit
    )
    tool = CountingTool()

    with pytest.raises(AuditPersistenceError, match="audit_persistence_failure"):
        _one_step(_agent(recording, tool))

    assert tool.calls == 0
    assert recording.requests[0].status == "audit_persistence_failure"


def test_audit_is_external_to_checkpoint_runtime_and_model_identity(tmp_path: Path) -> None:
    runtime = RuntimeState(seed=7, budget=BudgetState(50_000, 12, 900.0))
    recording, audit = _recording(
        tmp_path, ScriptedProviderModel([_structured_message()]), runtime=runtime
    )
    agent = _agent(recording, CountingTool())
    before_snapshot = AgentStateAdapter.capture(agent)
    before_runtime = runtime.to_dict()
    before_identity = recording.model_fingerprint

    audit.record_provider_failure(
        context=_context(),
        audit_request_id="local-isolation-check",
        input_hash="a" * 64,
        generation_config_hash="b" * 64,
        request_started_at="2026-11-24T00:00:00+00:00",
        latency_seconds=0.25,
        exception=TimeoutError("synthetic timeout"),
    )

    after_snapshot = AgentStateAdapter.capture(agent)
    assert AGENT_STATE_SCHEMA_VERSION == "2.0.0"
    assert after_snapshot.to_dict() == before_snapshot.to_dict()
    assert runtime.to_dict() == before_runtime
    assert recording.model_fingerprint == before_identity
    joined = json.dumps(after_snapshot.to_dict(), sort_keys=True)
    assert "local-isolation-check" not in joined


def test_concurrent_workers_keep_jsonl_lines_and_request_links_isolated(
    tmp_path: Path,
) -> None:
    audit = ProviderResponseAudit(tmp_path / "audit")

    def write(index: int) -> str:
        message = _structured_message()
        request_id = audit.new_request_id()
        audit.record_response(
            context=_context(f"worker-{index % 2}"),
            audit_request_id=request_id,
            input_hash=f"{index:064x}",
            generation_config_hash="c" * 64,
            message=message,
            request_started_at="2026-11-24T00:00:00+00:00",
            latency_seconds=0.01,
        )
        audit.record_parsing_outcome(message=message, parsing_status="success")
        return request_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        expected_ids = set(executor.map(write, range(40)))

    responses = _read_jsonl(audit.response_path)
    parsing = _read_jsonl(audit.parsing_path)
    response_ids = {item["audit_request_id"] for item in responses}
    parsing_ids = {item["audit_request_id"] for item in parsing}
    assert len(responses) == 40
    assert len(parsing) == 40
    assert response_ids == parsing_ids == expected_ids
    assert {item["worker_id"] for item in responses} == {"worker-0", "worker-1"}


def test_sensitive_values_are_redacted_without_removing_tool_debug_fields(
    tmp_path: Path,
) -> None:
    content = (
        "Bearer abc.def.ghi api_key=fixture-secret password=hunter2 "
        '<tool_call>{"name":"echo_value","arguments":{"value":"safe"}}</tool_call>'
    )
    message = _text_message(content)
    assert isinstance(message.raw, dict)
    message.raw["authorization"] = "Bearer top-secret"
    message.raw["Authorization Header"] = "Bearer header-secret"
    message.raw["api_key"] = "sk-1234567890abcdef"
    recording, audit = _recording(tmp_path, ScriptedProviderModel([message]))

    recording.generate([])

    serialized = audit.response_path.read_text(encoding="utf-8")
    assert "abc.def.ghi" not in serialized
    assert "fixture-secret" not in serialized
    assert "hunter2" not in serialized
    assert "top-secret" not in serialized
    assert "header-secret" not in serialized
    assert "sk-1234567890abcdef" not in serialized
    assert "echo_value" in serialized
    assert 'safe' in serialized
    assert "[REDACTED]" in serialized
    assert audit.response_path.stat().st_mode & 0o777 == 0o600
    assert audit.directory.stat().st_mode & 0o777 == 0o700
