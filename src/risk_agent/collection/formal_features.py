"""18 pre-approved features from frozen decision-time sources only."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from risk_agent.agent.agent_state_adapter import canonical_hash


NAMES = ("step_index", "tool_call_count", "observed_history_count", "remaining_step_budget",
    "unique_tool_count", "repeated_action_count", "repeated_action_ratio", "tool_sequence_length",
    "observed_error_count", "latest_observed_error_type", "historical_tool_success_rate",
    "consecutive_error_count", "token_budget_fraction", "tool_budget_fraction",
    "elapsed_time_fraction", "recent_tool_latency", "recent_model_latency", "average_tool_latency")


def visible_error(text: str) -> str:
    if not text.startswith("Error:\n") and "\nError:\n" not in text:
        return "none"
    lowered = text.lower()
    for marker, kind in (("no progress:", "no_progress"), ("timeout:", "timeout"),
        ("503 service unavailable", "unavailable"), ("invalid argument:", "invalid_argument"),
        ("conflict", "conflict")):
        if marker in lowered:
            return kind
    return "other"


def extract_decision_features(*, agent_payload: Mapping[str, Any], budget: Mapping[str, Any],
                              source_model_records: Sequence[Mapping[str, Any]],
                              source_tool_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    actions = [s for s in agent_payload["memory"]["steps"] if s["type"] == "action"]
    calls = [c for s in actions for c in (s.get("tool_calls") or [])]
    responses = [m for m in agent_payload["canonical_next_model_input"] if m["role"] == "tool-response"]
    def text(m):
        content = m["content"]
        return content if isinstance(content, str) else "".join(c.get("text", "") for c in content)
    errors = [visible_error(text(m)) for m in responses]
    signatures = [canonical_hash({"name": c["name"], "arguments": c["arguments"]}) for c in calls]
    repeated = len(signatures) - len(set(signatures))
    consecutive = 0
    for error in reversed(errors):
        if error == "none": break
        consecutive += 1
    tool_latencies = [r["latency_seconds"] for r in source_tool_records]
    model_latencies = [r["latency_seconds"] for r in source_model_records]
    if not model_latencies or any(x is None for x in model_latencies + tool_latencies):
        raise ValueError("Decision-time latency provenance missing")
    if any(r.get("prompt_tokens") is None or r.get("completion_tokens") is None for r in source_model_records):
        raise ValueError("Decision-time token usage provenance missing")
    tokens = sum(r["prompt_tokens"] + r["completion_tokens"] for r in source_model_records)
    if tokens != budget["tokens_used"]:
        raise ValueError("Decision-time token budget disagrees with Source usage")
    n = len(calls)
    values = dict(zip(NAMES, [agent_payload["agent"]["step_number"], n, len(responses),
        agent_payload["agent"]["remaining_steps"], len({c["name"] for c in calls}), repeated,
        repeated / n if n else 0, n, sum(e != "none" for e in errors), errors[-1] if errors else "none",
        sum(e == "none" for e in errors) / len(errors) if errors else None, consecutive,
        tokens / budget["max_tokens"], n / budget["max_tool_calls"],
        budget["latency_seconds_used"] / budget["max_latency_seconds"],
        tool_latencies[-1] if tool_latencies else None, model_latencies[-1],
        sum(tool_latencies) / len(tool_latencies) if tool_latencies else None], strict=True))
    provenance = {
        "agent_state_payload_sha256": canonical_hash(agent_payload),
        "canonical_messages_sha256": canonical_hash(agent_payload["canonical_next_model_input"]),
        "source_model_projection_sha256": canonical_hash([{k: r[k] for k in
            ("input_hash", "status", "prompt_tokens", "completion_tokens", "latency_seconds")}
            for r in source_model_records]),
        "source_tool_latency_projection_sha256": canonical_hash(tool_latencies),
        "budget_projection_sha256": canonical_hash({k: budget[k] for k in
            ("tokens_used", "max_tokens", "max_tool_calls", "latency_seconds_used", "max_latency_seconds")}),
        "cutoff_step": agent_payload["agent"]["step_number"], "future_data_excluded": True,
        "no_tool_event_status": "not_occurred" if not tool_latencies else "observed",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"feature_schema_version": "formal-decision-features-v1", "feature_names": list(NAMES),
        "values": values, "features_sha256": canonical_hash(values), "provenance": provenance}
