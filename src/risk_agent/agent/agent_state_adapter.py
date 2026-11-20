"""Versioned, JSON-safe snapshots of a smolagents ``ToolCallingAgent``.

The adapter deliberately serializes framework state, not live Python objects.  Model
clients and tool instances must be rebuilt by the caller before :meth:`restore`.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Mapping

from PIL import Image
from smolagents import ToolCallingAgent
from smolagents.memory import AgentMemory, ActionStep, PlanningStep, TaskStep, ToolCall
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
)
from smolagents.monitoring import Timing, TokenUsage


AGENT_STATE_SCHEMA_VERSION = "2.0.0"
_TYPE_KEY = "__risk_agent_type__"


class AgentStateAdapterError(ValueError):
    """Raised when agent state cannot be captured or safely restored."""


class AgentStateIntegrityError(AgentStateAdapterError):
    """Raised when serialized state or its reconstructed messages do not match."""


class _RestoredAgentError(Exception):
    """Minimal error representation compatible with ``ActionStep.dict``."""

    def __init__(self, message: str, original_type: str) -> None:
        super().__init__(message)
        self.original_type = original_type

    def dict(self) -> dict[str, str]:
        return {"type": self.original_type, "message": str(self)}


@dataclass(frozen=True, slots=True)
class AgentStateSnapshot:
    """A self-verifying, JSON-serializable agent-state envelope."""

    schema_version: str
    payload: dict[str, Any]
    agent_state_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "payload": json.loads(json.dumps(self.payload, ensure_ascii=False)),
            "agent_state_hash": self.agent_state_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AgentStateSnapshot:
        try:
            snapshot = cls(
                schema_version=str(value["schema_version"]),
                payload=dict(value["payload"]),
                agent_state_hash=str(value["agent_state_hash"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentStateAdapterError("Invalid agent-state snapshot envelope") from exc
        AgentStateAdapter.verify_state_hash(snapshot)
        return snapshot


def _qualified_name(value: object) -> str:
    cls = value.__class__
    return f"{cls.__module__}.{cls.__qualname__}"


def _encode(value: Any) -> Any:
    """Encode supported values without falling back to lossy ``str(value)``."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AgentStateAdapterError("Non-finite floats are not valid checkpoint state")
        return value
    if isinstance(value, bytes):
        return {_TYPE_KEY: "bytes", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return {_TYPE_KEY: "tuple", "items": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise AgentStateAdapterError("Checkpoint mappings require string keys")
        if _TYPE_KEY in value:
            raise AgentStateAdapterError(f"Reserved checkpoint key is not allowed: {_TYPE_KEY}")
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, Image.Image):
        buffer = io.BytesIO()
        value.save(buffer, format="PNG")
        return {
            _TYPE_KEY: "pil_image_png",
            "base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        }
    raise AgentStateAdapterError(
        f"Unsupported checkpoint value {type(value).__module__}.{type(value).__qualname__}"
    )


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    encoded_type = value.get(_TYPE_KEY)
    if encoded_type is None:
        return {key: _decode(item) for key, item in value.items()}
    if encoded_type == "bytes" and set(value) == {_TYPE_KEY, "base64"}:
        return base64.b64decode(value["base64"], validate=True)
    if encoded_type == "tuple" and set(value) == {_TYPE_KEY, "items"}:
        return tuple(_decode(item) for item in value["items"])
    if encoded_type == "pil_image_png" and set(value) == {_TYPE_KEY, "base64"}:
        image = Image.open(io.BytesIO(base64.b64decode(value["base64"], validate=True)))
        image.load()
        return image
    raise AgentStateAdapterError(f"Invalid encoded checkpoint value: {encoded_type!r}")


def canonical_json(value: Any) -> str:
    """Return the canonical JSON representation used by all adapter hashes."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _serialize_token_usage(usage: TokenUsage | None) -> dict[str, int] | None:
    if usage is None:
        return None
    return {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}


def _deserialize_token_usage(value: Mapping[str, Any] | None) -> TokenUsage | None:
    if value is None:
        return None
    return TokenUsage(input_tokens=int(value["input_tokens"]), output_tokens=int(value["output_tokens"]))


def serialize_chat_message(message: ChatMessage) -> dict[str, Any]:
    """Serialize the model-visible parts of a ChatMessage exactly.

    ``raw`` is intentionally omitted because it is a provider transport object and is
    not read by ``write_memory_to_messages``.
    """

    return {
        "role": message.role.value,
        "content": _encode(message.content),
        "tool_calls": [
            {
                "id": call.id,
                "type": call.type,
                "function": {
                    "name": call.function.name,
                    "arguments": _encode(call.function.arguments),
                    "description": call.function.description,
                },
            }
            for call in message.tool_calls or []
        ]
        if message.tool_calls is not None
        else None,
        "token_usage": _serialize_token_usage(message.token_usage),
    }


def deserialize_chat_message(value: Mapping[str, Any]) -> ChatMessage:
    tool_calls_value = value.get("tool_calls")
    tool_calls = None
    if tool_calls_value is not None:
        tool_calls = [
            ChatMessageToolCall(
                id=str(call["id"]),
                type=str(call["type"]),
                function=ChatMessageToolCallFunction(
                    name=str(call["function"]["name"]),
                    arguments=_decode(call["function"]["arguments"]),
                    description=call["function"].get("description"),
                ),
            )
            for call in tool_calls_value
        ]
    return ChatMessage(
        role=MessageRole(str(value["role"])),
        content=_decode(value.get("content")),
        tool_calls=tool_calls,
        token_usage=_deserialize_token_usage(value.get("token_usage")),
        raw=None,
    )


def _serialize_timing(timing: Timing) -> dict[str, float | None]:
    return {"start_time": timing.start_time, "end_time": timing.end_time}


def _deserialize_timing(value: Mapping[str, Any]) -> Timing:
    end_time = value.get("end_time")
    return Timing(
        start_time=float(value["start_time"]),
        end_time=float(end_time) if end_time is not None else None,
    )


def serialize_memory_step(step: TaskStep | ActionStep | PlanningStep) -> dict[str, Any]:
    if isinstance(step, TaskStep):
        return {
            "type": "task",
            "task": step.task,
            "task_images": _encode(step.task_images),
        }
    if isinstance(step, ActionStep):
        error = None
        if step.error is not None:
            error = {
                "type": getattr(step.error, "original_type", step.error.__class__.__name__),
                "message": str(step.error),
            }
        return {
            "type": "action",
            "step_number": step.step_number,
            "timing": _serialize_timing(step.timing),
            "model_input_messages": [serialize_chat_message(item) for item in step.model_input_messages]
            if step.model_input_messages is not None
            else None,
            "tool_calls": [
                {"name": call.name, "arguments": _encode(call.arguments), "id": call.id}
                for call in step.tool_calls
            ]
            if step.tool_calls is not None
            else None,
            "error": error,
            "model_output_message": serialize_chat_message(step.model_output_message)
            if step.model_output_message is not None
            else None,
            "model_output": _encode(step.model_output),
            "code_action": step.code_action,
            "observations": step.observations,
            "observations_images": _encode(step.observations_images),
            "action_output": _encode(step.action_output),
            "token_usage": _serialize_token_usage(step.token_usage),
            "is_final_answer": step.is_final_answer,
        }
    if isinstance(step, PlanningStep):
        return {
            "type": "planning",
            "model_input_messages": [serialize_chat_message(item) for item in step.model_input_messages],
            "model_output_message": serialize_chat_message(step.model_output_message),
            "plan": step.plan,
            "timing": _serialize_timing(step.timing),
            "token_usage": _serialize_token_usage(step.token_usage),
        }
    raise AgentStateAdapterError(f"Unsupported memory step: {_qualified_name(step)}")


def deserialize_memory_step(value: Mapping[str, Any]) -> TaskStep | ActionStep | PlanningStep:
    step_type = value.get("type")
    if step_type == "task":
        return TaskStep(task=str(value["task"]), task_images=_decode(value.get("task_images")))
    if step_type == "action":
        error_value = value.get("error")
        error = None
        if error_value is not None:
            error = _RestoredAgentError(str(error_value["message"]), str(error_value["type"]))
        input_messages = value.get("model_input_messages")
        tool_calls = value.get("tool_calls")
        output_message = value.get("model_output_message")
        return ActionStep(
            step_number=int(value["step_number"]),
            timing=_deserialize_timing(value["timing"]),
            model_input_messages=[deserialize_chat_message(item) for item in input_messages]
            if input_messages is not None
            else None,
            tool_calls=[
                ToolCall(name=str(item["name"]), arguments=_decode(item["arguments"]), id=str(item["id"]))
                for item in tool_calls
            ]
            if tool_calls is not None
            else None,
            error=error,  # type: ignore[arg-type]
            model_output_message=deserialize_chat_message(output_message)
            if output_message is not None
            else None,
            model_output=_decode(value.get("model_output")),
            code_action=value.get("code_action"),
            observations=value.get("observations"),
            observations_images=_decode(value.get("observations_images")),
            action_output=_decode(value.get("action_output")),
            token_usage=_deserialize_token_usage(value.get("token_usage")),
            is_final_answer=bool(value.get("is_final_answer", False)),
        )
    if step_type == "planning":
        return PlanningStep(
            model_input_messages=[deserialize_chat_message(item) for item in value["model_input_messages"]],
            model_output_message=deserialize_chat_message(value["model_output_message"]),
            plan=str(value["plan"]),
            timing=_deserialize_timing(value["timing"]),
            token_usage=_deserialize_token_usage(value.get("token_usage")),
        )
    raise AgentStateAdapterError(f"Unsupported memory step type: {step_type!r}")


def _tool_registry(agent: ToolCallingAgent) -> list[dict[str, Any]]:
    return [
        {
            "position": position,
            "registry_key": key,
            "class": _qualified_name(tool),
            "name": tool.name,
            "description": tool.description,
            "inputs": _encode(tool.inputs),
            "output_type": tool.output_type,
            "output_schema": _encode(getattr(tool, "output_schema", None)),
        }
        for position, (key, tool) in enumerate(agent.tools.items())
    ]


def _model_config(agent: ToolCallingAgent) -> dict[str, Any]:
    model = agent.model
    # ``client_kwargs`` contains the API key and live HTTP client and is never read.
    generation = getattr(model, "kwargs", {})
    if not isinstance(generation, Mapping):
        raise AgentStateAdapterError("Model generation kwargs must be a mapping")
    return {
        "class": _qualified_name(model),
        "model_id": getattr(model, "model_id", None),
        "flatten_messages_as_text": getattr(model, "flatten_messages_as_text", False),
        "tool_name_key": getattr(model, "tool_name_key", "name"),
        "tool_arguments_key": getattr(model, "tool_arguments_key", "arguments"),
        "custom_role_conversions": _encode(getattr(model, "custom_role_conversions", None)),
        "generation": _encode(dict(generation)),
    }


def _without_transport_raw(value: Any) -> Any:
    """Remove provider response objects from the framework's audit projection."""

    if isinstance(value, list):
        return [_without_transport_raw(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_without_transport_raw(item) for item in value)
    if isinstance(value, Mapping):
        return {
            key: _without_transport_raw(item)
            for key, item in value.items()
            if key != "raw"
        }
    return value


class AgentStateAdapter:
    """Capture and restore complete model-visible smolagents state."""

    @staticmethod
    def capture(agent: ToolCallingAgent) -> AgentStateSnapshot:
        messages = [serialize_chat_message(item) for item in agent.write_memory_to_messages()]
        memory_steps = [serialize_memory_step(item) for item in agent.memory.steps]
        prompt_templates = _encode(dict(agent.prompt_templates))
        payload = {
            "framework": {"name": "smolagents", "version": version("smolagents")},
            "agent": {
                "class": _qualified_name(agent),
                "agent_name": agent.agent_name,
                "name": agent.name,
                "description": agent.description,
                "instructions": agent.instructions,
                "task": agent.task,
                "step_number": agent.step_number,
                "max_steps": agent.max_steps,
                "remaining_steps": max(0, agent.max_steps - agent.step_number),
                "planning_interval": agent.planning_interval,
                "stream_outputs": agent.stream_outputs,
                "state": _encode(agent.state),
            },
            "model": _model_config(agent),
            "tool_registry": _tool_registry(agent),
            "prompt_templates": prompt_templates,
            "prompt_templates_hash": canonical_hash(prompt_templates),
            "memory": {
                "system_prompt": agent.memory.system_prompt.system_prompt,
                "steps": memory_steps,
                "audit_full_steps": _encode(
                    _without_transport_raw(agent.memory.get_full_steps())
                ),
            },
            "canonical_next_model_input": messages,
            "next_model_input_hash": canonical_hash(messages),
        }
        return AgentStateSnapshot(
            schema_version=AGENT_STATE_SCHEMA_VERSION,
            payload=payload,
            agent_state_hash=canonical_hash(payload),
        )

    @staticmethod
    def verify_state_hash(snapshot: AgentStateSnapshot) -> None:
        if snapshot.schema_version != AGENT_STATE_SCHEMA_VERSION:
            raise AgentStateAdapterError(
                f"Unsupported agent-state schema {snapshot.schema_version!r}; "
                f"expected {AGENT_STATE_SCHEMA_VERSION!r}"
            )
        actual = canonical_hash(snapshot.payload)
        if actual != snapshot.agent_state_hash:
            raise AgentStateIntegrityError(
                f"Agent state hash mismatch: expected {snapshot.agent_state_hash}, got {actual}"
            )

    @staticmethod
    def restore(agent: ToolCallingAgent, snapshot: AgentStateSnapshot) -> ToolCallingAgent:
        """Restore into a freshly rebuilt agent and verify its next model input."""

        AgentStateAdapter.verify_state_hash(snapshot)
        payload = snapshot.payload
        framework = payload["framework"]
        if framework != {"name": "smolagents", "version": version("smolagents")}:
            raise AgentStateAdapterError(f"Framework identity mismatch: {framework!r}")
        expected_agent = payload["agent"]
        if expected_agent["class"] != _qualified_name(agent):
            raise AgentStateAdapterError(
                f"Agent class mismatch: expected {expected_agent['class']}, got {_qualified_name(agent)}"
            )
        if payload["model"] != _model_config(agent):
            raise AgentStateAdapterError("Rebuilt model identity or generation configuration differs")
        if payload["tool_registry"] != _tool_registry(agent):
            raise AgentStateAdapterError("Rebuilt tool registry differs")
        current_templates = _encode(dict(agent.prompt_templates))
        if canonical_hash(current_templates) != payload["prompt_templates_hash"]:
            raise AgentStateAdapterError("Rebuilt prompt templates differ")

        max_steps = int(expected_agent["max_steps"])
        step_number = int(expected_agent["step_number"])
        if int(expected_agent["remaining_steps"]) != max(0, max_steps - step_number):
            raise AgentStateIntegrityError("Stored remaining step budget is inconsistent")

        memory_value = payload["memory"]
        memory = AgentMemory(system_prompt=str(memory_value["system_prompt"]))
        memory.steps = [deserialize_memory_step(item) for item in memory_value["steps"]]
        agent.memory = memory
        agent.task = expected_agent["task"]
        agent.step_number = step_number
        agent.max_steps = max_steps
        agent.planning_interval = expected_agent["planning_interval"]
        agent.stream_outputs = bool(expected_agent["stream_outputs"])
        agent.state = _decode(expected_agent["state"])
        agent.name = expected_agent["name"]
        agent.description = expected_agent["description"]
        agent.instructions = expected_agent["instructions"]

        restored_messages = [serialize_chat_message(item) for item in agent.write_memory_to_messages()]
        restored_hash = canonical_hash(restored_messages)
        if restored_hash != payload["next_model_input_hash"]:
            raise AgentStateIntegrityError(
                "Restored next model input differs from the captured canonical message list"
            )
        if restored_messages != payload["canonical_next_model_input"]:
            raise AgentStateIntegrityError("Restored canonical messages differ despite hash verification")
        return agent


__all__ = [
    "AGENT_STATE_SCHEMA_VERSION",
    "AgentStateAdapter",
    "AgentStateAdapterError",
    "AgentStateIntegrityError",
    "AgentStateSnapshot",
    "canonical_hash",
    "canonical_json",
    "deserialize_chat_message",
    "deserialize_memory_step",
    "serialize_chat_message",
    "serialize_memory_step",
]
