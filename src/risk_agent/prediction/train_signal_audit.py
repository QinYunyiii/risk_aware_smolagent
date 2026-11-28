"""Descriptive Train audit. Candidate rules are independent of outcome labels."""
from __future__ import annotations

from collections import Counter
from itertools import combinations
import json
import math
import re
from typing import Any

import numpy as np

from risk_agent.collection.formal_features import NAMES
from .train_public_signals import review_public


def distribution(values: list[Any]) -> dict:
    present = [v for v in values if v is not None]
    counts = Counter(json.dumps(v, sort_keys=True) for v in present)
    result = {"n": len(values), "non_missing_count": len(present),
        "missing_rate": 1 - len(present) / len(values) if values else None,
        "unique_values": len(counts), "value_counts": dict(sorted(counts.items()))}
    if present and all(type(v) in (int, float) for v in present):
        if not all(math.isfinite(v) for v in present):
            raise ValueError("Nonfinite audit value")
        result.update(min=min(present), max=max(present), mean=float(np.mean(present)),
                      std_population=0.0 if len(counts) == 1 else float(np.std(present, ddof=0)))
    else:
        result.update(min=None, max=None, mean=None, std_population=None)
    return result


def describe_column(rows: list[dict], values: list[Any]) -> dict:
    result = distribution(values)
    result["by_label"] = {str(y): distribution([v for r, v in zip(rows, values, strict=True) if r["y_failure"] == y])
                          for y in (0, 1)}
    result["by_template"] = {t: distribution([v for r, v in zip(rows, values, strict=True) if r["template_id"] == t])
                             for t in sorted({r["template_id"] for r in rows})}
    result["within_template_variation_count"] = sum(d["unique_values"] > 1 for d in result["by_template"].values())
    return result


def relationships(columns: dict[str, list[Any]]) -> dict:
    exact, correlations = [], []
    for a, b in combinations(columns, 2):
        first, second = columns[a], columns[b]
        if first == second:
            exact.append({"first": a, "second": b, "constant_pair": len(set(map(str, first))) <= 1})
        pairs = [(x, y) for x, y in zip(first, second, strict=True)
                 if type(x) in (int, float) and type(y) in (int, float)]
        if len(pairs) >= 2:
            x, y = np.array(pairs, dtype=float).T
            if len(set(x)) > 1 and len(set(y)) > 1:
                rho = float(np.corrcoef(x, y)[0, 1])
                if abs(rho) >= .95:
                    correlations.append({"first": a, "second": b, "pearson_r": rho, "pairwise_n": len(pairs)})
    return {"exact_duplicate_pairs": exact, "high_numeric_correlations": correlations,
        "audit_correlation_cutoff": .95, "constant_correlations": "undefined, not set to one",
        "categorical_correlations": "not assigned arbitrary numeric category codes"}


def public_projection(row: dict) -> dict:
    payload = row["payload"]
    def text(m):
        c = m["content"]
        return c if isinstance(c, str) else "".join(x.get("text", "") for x in c)
    messages = payload["canonical_next_model_input"]
    task = payload["agent"]["task"]
    if not any(task in text(m) for m in messages if m["role"] == "user"):
        raise ValueError("Task is not part of captured public input")
    system = "\n".join(text(m) for m in messages if m["role"] == "system")
    if not all(t["name"] in system and t["description"] in system for t in payload["tool_registry"]):
        raise ValueError("Captured tool descriptions cannot be confirmed in public system prompt")
    return {"split": "train", "task_text": task, "cutoff_step": payload["agent"]["step_number"],
        "calls": [{k: c[k] for k in ("name", "arguments")} for s in payload["memory"]["steps"]
                  if s["type"] == "action" for c in (s.get("tool_calls") or [])],
        "responses": [text(m) for m in messages if m["role"] == "tool-response"],
        "tool_registry": [{k: t[k] for k in ("name", "description", "inputs", "output_type")}
                          for t in payload["tool_registry"]]}


def signals(view: dict, monitor: dict) -> tuple[dict, dict]:
    """Strict interfaces: label/fault/future fields cannot be passed to rules."""
    if set(monitor) != {"cutoff_verified", "model_requests", "completion_limit"} or monitor["cutoff_verified"] is not True:
        raise ValueError("Only verified completed Source monitor events permitted")
    if any(set(r) != {"completion_tokens"} for r in monitor["model_requests"]):
        raise ValueError("Unexpected/private monitor fields")
    base = review_public(view)
    if base["error_parse_status"] == "UNKNOWN":
        raise ValueError("Cannot assign semantics to an unrecognized public response")
    # Mask opaque paths, quoted values and identifier-like strings before lexical
    # prompt rules. Dataset names (including allocation-like words) are not signals.
    task = view["task_text"].split("\n\n", 1)[0]
    task = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', '<literal>', task)
    task = re.sub(r"\b(?:data|workspace)/\S+", "<path>", task)
    task = re.sub(r"\b[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9_-]+\b", "<identifier>", task)
    dependency = int(bool(re.search(r"\bthen\b|\bpreceding\b|\breturned (?:records|paths|value|status)\b|\breturned by\b", task, re.I)))
    write_intent = int(bool(re.search(r"\b(?:update|move|write|create|delete)\b", task, re.I)))
    registry = {s["name"]: s for s in view["tool_registry"]}
    missing = extra = empty = types = 0
    for call in view["calls"]:
        schema, args = registry[call["name"]]["inputs"], call["arguments"]
        missing += sum(k not in args for k, spec in schema.items() if not spec.get("nullable", False))
        extra += len(set(args) - set(schema))
        for key, value in args.items():
            if key not in schema:
                continue
            spec = schema[key]
            empty += int(value is None or isinstance(value, str) and not value.strip())
            allowed = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
                       "object": dict, "array": list}
            kind = spec.get("type")
            if kind in allowed and not (value is None and spec.get("nullable", False)):
                types += int(not isinstance(value, allowed[kind]) or kind in ("integer", "number") and type(value) is bool)
    response = view["responses"][-1]
    normal = response.startswith("Observation:\n")
    value = json.loads(response[len("Observation:\n"):]) if normal else None
    # This is a literal, public declaration of a status dependency, not an
    # inferred plan or hidden environment truth. Other prompts are not applicable.
    status_dependency = bool(re.search(r"\bstatus returned by\b", task, re.I)) and write_intent == 1
    schema_declares_status = any("expected_status" in s["inputs"] for s in view["tool_registry"])
    gap = (int(not (normal and isinstance(value, dict) and value.get("status") not in (None, "")))
           if status_dependency and schema_declares_status else None)
    limit = monitor["completion_limit"]
    if type(limit) not in (int, float) or limit <= 0 or not monitor["model_requests"]:
        raise ValueError("Missing captured model completion limit")
    fraction = monitor["model_requests"][-1]["completion_tokens"] / limit
    call = view["calls"][-1]
    result = {"public_completion_unknown": base["public_completion_unknown"],
        "public_unknown_resource": base["public_unknown_resource"],
        "prompt_explicit_dependency": dependency, "declared_state_dependency_gap": gap,
        "recent_completion_limit_fraction": fraction,
        "schema_argument_violation_count": missing + extra + empty + types,
        "literal_identifier_mismatch": int(base["literal_identifier_mismatches"] > 0) if base["literal_identifier_pairs"] else None,
        "observation_empty": int(value is None or value == "" or isinstance(value, (list, dict)) and len(value) == 0) if normal else None,
        "observation_array_count": len(value) if normal and isinstance(value, list) else None,
        "completed_model_request_count": len(monitor["model_requests"]),
        "current_call_idempotency_key_present": int("idempotency_key" in call["arguments"]),
        "tool_switch_count": sum(a["name"] != b["name"] for a, b in zip(view["calls"], view["calls"][1:]))}
    diagnostics = {"prompt_write_intent": write_intent, "status_dependency_applicable": int(gap is not None),
        "schema_missing": missing, "schema_extra": extra, "schema_empty": empty, "schema_type": types,
        "json_type": base["observation_json_type"] or "not_applicable_error",
        "public_error_parse": base["error_parse_status"], "redacted_task": task,
        "called_tool_declares_state_change": int(bool(re.search(r"\b(?:atomically update|modify|delete|write)\b",
            registry[call["name"]].get("description", ""), re.I))),
        "returned_resource_identifier_comparable": int(normal and isinstance(value, dict)
            and any(k in value and k in call["arguments"] for k in ("order_id", "sku"))),
        "returned_resource_identifier_literal_difference": (int(any(value[k] != call["arguments"][k]
            for k in ("order_id", "sku") if k in value and k in call["arguments"]))
            if normal and isinstance(value, dict) and any(k in value and k in call["arguments"] for k in ("order_id", "sku")) else None)}
    return result, diagnostics


# Recommendations are fixed semantic/availability judgments, never selected by
# the descriptive success/failure distributions computed below.
SPECS = [
    ("public_completion_unknown", "observation", "错误正文明确声明完成状态未知", "I(public error body contains completion status is unknown)", ["agent"], "CANDIDATE", "unknown commit state makes subsequent writes unsafe", "high: only sparse templates; not proof of generalization"),
    ("declared_state_dependency_gap", "prompt_observation", "Prompt 明示用返回 status 作写入前提，但最新观察不含可用 status", "I(no public status) conditional on explicit declared status dependency; else null", ["agent"], "CONDITIONAL", "declared compare-and-set precondition cannot yet be filled from latest response", "high: currently API-only and may duplicate explicit-error signals"),
    ("prompt_explicit_dependency", "prompt_static", "脱敏首段含明确顺序或返回值依赖连接词", "I(then or preceding or returned-result connector in masked task)", ["agent"], "CONDITIONAL", "dependent operations can propagate an erroneous intermediate result", "very high: static template/task-construction proxy"),
    ("recent_completion_limit_fraction", "monitor", "最近已完成 Source 请求的 Completion Tokens / 捕获的单次 Completion 上限", "completion_tokens_last / captured_generation.max_tokens", ["agent", "models"], "CONDITIONAL", "generation budget pressure may truncate a future structured response; association not established", "medium/high: prompt/schema/output verbosity may explain variation"),
    ("public_unknown_resource", "observation", "错误正文明确指出未知资源", "I(error message starts Unknown order/sku/file/resource:)", ["agent"], "CONDITIONAL", "wrong resource reference can obstruct task continuation", "high: only a few templates; redundant with other candidate and old categories"),
    ("schema_argument_violation_count", "schema_call", "必填缺失、多余、空值和基本类型违例总数", "missing + extra + empty + primitive_type_violations", ["agent"], "REJECT", "invalid calls can fail before normal observation", "current qualified cohort may structurally exclude failed-to-parse calls"),
    ("literal_identifier_mismatch", "prompt_call", "公开 exact 标识符字面不一致，仅在可比较时有值", "I(argument != unique literal prompt identifier), else null", ["agent"], "CONDITIONAL", "identifier mismatch may select an unintended resource; normalization can be legitimate", "high: narrow applicability and literal mismatch is not semantic error"),
    ("observation_empty", "observation", "正常 JSON 观察为 null、空字符串、空列表或空对象", "I(normal JSON is explicitly empty), else null", ["agent"], "REJECT", "empty intermediate data might prevent a dependent action", "task-dependent; no reference expected cardinality"),
    ("observation_array_count", "observation", "正常数组观察中的元素数，不判定异常", "len(array) if normal JSON array else null", ["agent"], "REJECT", "cardinality could expose incomplete results only with a public expected count", "very high: size is a dataset/task generator proxy without normative count"),
    ("completed_model_request_count", "monitor", "Checkpoint 前已完成的 Source Model 请求数", "len(completed captured Source requests)", ["models", "budget"], "REJECT", "consumption approaches the model-call budget", "constant at current first eligible step and duplicates existing counts"),
    ("current_call_idempotency_key_present", "schema_call", "当前调用包含幂等键，不断言幂等机制生效", "I(idempotency_key in public call arguments)", ["agent"], "REJECT", "a declared key may help prevent repeated write effects", "very high: currently mandatory for one tool/template; presence is not verified semantics"),
    ("tool_switch_count", "call_history", "已发生相邻工具调用名称改变次数", "sum(name_i != name_(i-1))", ["agent"], "REJECT", "unproductive switching could signal planning difficulty", "constant at one-call checkpoint; no temporal support"),
]

SOURCE_FIELDS = {
    "public_completion_unknown": "payload.canonical_next_model_input[role=tool-response][-1].content; matched to memory.steps.action.tool_calls[-1]",
    "public_unknown_resource": "payload.canonical_next_model_input[role=tool-response][-1].content; matched to memory.steps.action.tool_calls[-1]",
    "declared_state_dependency_gap": "payload.agent.task first paragraph; tool_registry.inputs.expected_status; canonical_next_model_input[-1] public JSON.status",
    "prompt_explicit_dependency": "payload.agent.task first paragraph after masking paths, quoted literals and identifier-like strings",
    "recent_completion_limit_fraction": "source_model_requests[-1].completion_tokens / payload.model.generation.max_tokens",
    "schema_argument_violation_count": "payload.tool_registry.inputs / memory.steps.action.tool_calls.arguments",
    "literal_identifier_mismatch": "payload.agent.task first paragraph / tool_registry.inputs.description / memory.steps.action.tool_calls.arguments",
    "observation_empty": "payload.canonical_next_model_input[role=tool-response][-1].content normal Observation JSON",
    "observation_array_count": "payload.canonical_next_model_input[role=tool-response][-1].content normal Observation JSON array",
    "completed_model_request_count": "source_model_requests length; captured runtime/budget_state.model_calls_used",
    "current_call_idempotency_key_present": "payload.memory.steps.action.tool_calls[-1].arguments keys",
    "tool_switch_count": "payload.memory.steps.action.tool_calls.name sequence",
}


def validate_candidate(candidate: dict) -> None:
    from .train_evidence import permitted_member
    if (candidate["recommendation"] not in {"CANDIDATE", "CONDITIONAL", "REJECT"}
            or candidate["decision_time_available"] is not True
            or candidate["depends_on_future_data"] is not False
            or candidate["depends_on_hidden_metadata"] is not False):
        raise ValueError("Unavailable, future or hidden candidate is inadmissible")
    if re.search(r"fault_|future_|outcome|verifier|expected_answer", json.dumps(candidate["source_field"]), re.I):
        raise ValueError("Forbidden candidate source field")
    for path in candidate["source_file"]:
        parts = path.split("/")
        if len(parts) < 4 or parts[0] != "runs" or not permitted_member("/".join(parts[3:])):
            raise ValueError("Forbidden candidate source artifact")
    if not candidate["source_record_sha256"] or any(not re.fullmatch("[a-f0-9]{64}", h) for h in candidate["source_record_sha256"]):
        raise ValueError("Candidate must trace to actual canonical Checkpoint records")


def build_audit(rows: list[dict]) -> tuple[dict, dict, list[dict]]:
    if not rows:
        raise ValueError("No Train cohort")
    columns = {n: [r["features"][n] for r in rows] for n in NAMES}
    existing = {n: describe_column(rows, values) for n, values in columns.items()}
    monitor_only = {"remaining_step_budget", "token_budget_fraction", "tool_budget_fraction",
        "elapsed_time_fraction", "recent_tool_latency", "recent_model_latency", "average_tool_latency"}
    for name, entry in existing.items():
        entry.update(decision_time_available=True, agent_visible=name not in monitor_only,
            monitor_observable=True, depends_on_future_data=False, depends_on_hidden_metadata=False,
            source_field="recomputed by frozen extract_decision_features from captured Agent, budget and completed Source events")
    existing_doc = {"cohort_rows": len(rows), "features": existing, "relationships": relationships(columns),
        "constant_features": [n for n, s in existing.items() if s["unique_values"] <= 1],
        "std_definition": "population ddof=0", "label_source": "qualified Train risk labels",
        "decision_time_available": True, "all_18_recomputed_from_checkpoint": True}
    computed = []
    for row in rows:
        view = public_projection(row)
        limit = row["payload"]["model"]["generation"]["max_tokens"]
        values, diagnostics = signals(view, {"cutoff_verified": True,
            "model_requests": [{"completion_tokens": m["completion_tokens"]} for m in row["models"]],
            "completion_limit": limit})
        computed.append({"task_id": row["task_id"], "template_id": row["template_id"],
            "checkpoint_id": row["checkpoint_id"], "values": values, "diagnostics": diagnostics,
            "temporal": row["temporal"]})
    candidates = []
    for name, family, definition, equation, roles, recommendation, mechanism, shortcut in SPECS:
        values = [r["values"][name] for r in computed]
        stats = describe_column(rows, values)
        overlap = relationships({name: values, **columns})
        is_binary = all(v in (0, 1, None) for v in values)
        positive_templates = {r["template_id"] for r, v in zip(rows, values, strict=True) if v == 1} if is_binary else set()
        source_files = sorted({r["sources"][role]["path"] for r in rows for role in roles})
        candidates.append({"feature_name": name, "feature_family": family, "definition": definition,
            "mathematical_definition": equation, "source_file": source_files,
            "source_field": SOURCE_FIELDS[name],
            "source_roles": roles, "source_record_sha256": [r["checkpoint_record_sha256"] for r in rows],
            "decision_time_available": True, "agent_visible": family != "monitor", "monitor_observable": True,
            "depends_on_future_data": False, "depends_on_hidden_metadata": False,
            "train_non_missing_count": stats["non_missing_count"], "train_missing_rate": stats["missing_rate"],
            "train_unique_values": stats["unique_values"],
            "train_template_coverage": sum(d["non_missing_count"] > 0 for d in stats["by_template"].values()),
            "train_positive_count": sum(v == 1 for v in values) if is_binary else None,
            "train_positive_template_coverage": len(positive_templates) if is_binary else None,
            "existing_feature_overlap": [p for p in overlap["exact_duplicate_pairs"] if name in (p["first"], p["second"])],
            "high_existing_correlations": [p for p in overlap["high_numeric_correlations"] if name in (p["first"], p["second"])],
            "shortcut_risk": shortcut, "failure_mechanism": mechanism,
            "extraction_complexity": "deterministic local projection; no model or hidden-state query",
            "recommendation": recommendation, "statistics": stats,
            "created_when": "task/schema before execution; response at completed tool event; monitor at completed model event",
            "rule_selection": "predefined semantic rules, no label-score selection"})
        validate_candidate(candidates[-1])
    candidate_columns = {n: [r["values"][n] for r in computed] for n, *_ in SPECS}
    candidate_doc = {"status": "AUDIT_ONLY_NOT_ACTIVE_FEATURES", "cohort_rows": len(rows),
        "candidates": candidates, "candidate_relationships": relationships(candidate_columns),
        "source_records": [{"checkpoint_record_sha256": r["checkpoint_record_sha256"],
            "risk_record_sha256": r["risk_record_sha256"], "sources": r["sources"], "temporal": r["temporal"]} for r in rows],
        "source_digest_arrays": "Each source_record_sha256 is an actual CP canonical record digest, not a synthetic aggregate",
        "models_fit": 0, "validation_or_test_used": False, "current_formal_features_modified": False}
    return existing_doc, candidate_doc, computed
