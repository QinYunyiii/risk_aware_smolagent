"""Strict Train-only, manifest-bound decision-time evidence for offline audits."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> str:
    from risk_agent.agent.agent_state_adapter import canonical_hash
    return canonical_hash(value)


def permitted_member(name: str) -> bool:
    """Future outcomes, fault state, environments and private runtime are absent."""
    if name in {"run_manifest.json", "records/risk_candidates.jsonl", "records/checkpoints.jsonl",
                "train_eligibility_release.json"}:
        return True
    if re.fullmatch(r"tasks/[^/]+/(decision_features.json|source_model_requests.json|source_tools.jsonl)", name):
        return True
    return bool(re.fullmatch(r"source/checkpoints/[^/]+/(agent/agent_state.json|runtime/budget_state.json|runtime/execution_position.json)", name))


def source_directory(root: Path, directory: Path) -> Path:
    path = directory.resolve()
    if not path.is_relative_to((root / "runs").resolve()):
        raise ValueError("Train source must be under local run storage")
    relative = path.relative_to((root / "runs").resolve())
    if any(re.search(r"(?:^|[_-])(validation|test)(?:[_-]|$)", p.lower()) for p in relative.parts):
        raise ValueError("Validation/Test source directory forbidden")
    return path


class TrainReader:
    def __init__(self, root: Path, spec: dict[str, Any]):
        self.base = source_directory(root, Path(spec["directory"]))
        self.used: dict[str, str] = {}
        self.cache: dict[str, bytes] = {}
        identity = json.loads((self.base / "run_manifest.json").read_bytes())
        if (identity.get("authorized_split") != "train" or identity.get("run_id") != spec["run_id"]
                or identity.get("formal_experiment") is not True or identity.get("offline_fixture") is not False):
            raise ValueError("Only real formal Train identity permitted")
        manifest = (self.base / spec["artifact_manifest"]).resolve()
        if not manifest.is_relative_to(self.base) or digest(manifest.read_bytes()) != spec["artifact_manifest_sha256"]:
            raise ValueError("Train manifest hash mismatch")
        self.manifest_path, self.manifest_sha256 = manifest, spec["artifact_manifest_sha256"]
        entries = json.loads(manifest.read_bytes())["files"]
        self.index = {}
        for item in entries:
            path = (self.base / item["path"]).resolve()
            if not path.is_relative_to(self.base) or item["path"] in self.index:
                raise ValueError("Manifest path escape or duplicate entry")
            self.index[item["path"]] = item
        self.read("run_manifest.json")

    def read(self, name: str, *, lines: bool = False) -> Any:
        if not permitted_member(name):
            raise ValueError("Nondecision or hidden Train artifact forbidden")
        path = (self.base / name).resolve()
        if not path.is_relative_to(self.base) or name not in self.index:
            raise ValueError("Unbound decision-time source")
        if name not in self.cache:
            data = path.read_bytes()
            item = self.index[name]
            if len(data) != item["size_bytes"] or digest(data) != item["sha256"]:
                raise ValueError("Train source artifact hash mismatch")
            self.cache[name], self.used[name] = data, item["sha256"]
        data = self.cache[name]
        return [json.loads(s) for s in data.splitlines() if s] if lines else json.loads(data)

    def records(self, name: str) -> list[dict[str, Any]]:
        rows, previous = self.read(name, lines=True), None
        for row in rows:
            if (row.get("split") != "train" or row.get("previous_record_sha256") != previous
                    or canonical({k: v for k, v in row.items() if k != "record_sha256"}) != row["record_sha256"]):
                raise ValueError("Train split or append-only record chain mismatch")
            previous = row["record_sha256"]
        return rows


def validate_cutoff(payload: dict, budget: dict, models: list[dict], tools: list[dict]) -> dict:
    actions = [s for s in payload["memory"]["steps"] if s["type"] == "action"]
    calls = [c for s in actions for c in (s.get("tool_calls") or [])]
    step = payload["agent"]["step_number"]
    if (not actions or any(s["step_number"] > step or s.get("is_final_answer") for s in actions)
            or any(s["timing"].get("end_time") is None for s in actions)
            or budget["model_calls_used"] != len(models) or len(models) != len(actions)
            or budget["tool_calls_used"] != len(calls) or len(tools) != len(calls)):
        raise ValueError("Source events extend beyond or disagree with captured Memory")
    if any(r["status"] != "succeeded" or r["input_hash"] != canonical(s["model_input_messages"])
           for r, s in zip(models, actions, strict=True)):
        raise ValueError("Model event does not belong to completed captured step")
    from datetime import datetime
    end = max(s["timing"]["end_time"] for s in actions)
    if any(datetime.fromisoformat(t["timestamp"]).timestamp() > end for t in tools):
        raise ValueError("Tool event occurred after captured action completion")
    if ([t["tool_name"] for t in tools] != [c["name"] for c in calls]
            or any(t["tool_arguments"] != c["arguments"] for t, c in zip(tools, calls, strict=True))):
        raise ValueError("Source tool sequence differs from captured calls")
    if (sum(m["prompt_tokens"] for m in models) != budget["prompt_tokens_used"]
            or sum(m["completion_tokens"] for m in models) != budget["completion_tokens_used"]):
        raise ValueError("Checkpoint token usage differs from completed Source requests")
    return {"cutoff_step": step, "model_events": len(models), "tool_events": len(tools),
        "tool_event_timestamps_before_action_end": True, "model_input_hashes_match": True,
        "temporal_proof": "persisted CP + completed ActionStep + exact event counts/input hashes"}


def load_train(root: Path, proof_path: Path, proof_sha256: str) -> tuple[list[dict], dict]:
    """Use Train release and raw chains. No mixed snapshot, outcomes or catalogs."""
    root = root.resolve()
    proof_path = proof_path.resolve()
    if not proof_path.is_relative_to(root / "runs") or proof_path.name != "train_proof.json":
        raise ValueError("Only the dedicated local Train proof is accepted")
    if digest(proof_path.read_bytes()) != proof_sha256:
        raise ValueError("Train qualification proof hash mismatch")
    proof = json.loads(proof_path.read_bytes())
    readers = {}
    for spec in proof["runs"]:
        if spec["run_id"] in readers:
            reader = readers[spec["run_id"]]
            if reader.base != Path(spec["directory"]).resolve() or reader.manifest_sha256 != spec["artifact_manifest_sha256"]:
                raise ValueError("Conflicting duplicate Train source")
            continue
        readers[spec["run_id"]] = TrainReader(root, spec)
    release_path = Path(proof["release_path"]).resolve()
    matching = [(r, str(release_path.relative_to(r.base))) for r in readers.values() if release_path.is_relative_to(r.base)]
    if len(matching) != 1:
        raise ValueError("Train release is not in exactly one trusted source")
    reader, name = matching[0]
    release = reader.read(name)
    if (reader.used[name] != proof["release_sha256"] or release.get("split") != "train"
            or release.get("research_dataset_eligible") is not True or release.get("formal_experiment") is not True):
        raise ValueError("Train eligibility release mismatch")
    indices = {rid: ({r["record_sha256"]: r for r in reader.records("records/risk_candidates.jsonl")},
                     {r["record_sha256"]: r for r in reader.records("records/checkpoints.jsonl")})
               for rid, reader in readers.items()}
    selected, seen = [], set()
    from risk_agent.collection.formal_features import NAMES, extract_decision_features
    from risk_agent.prediction.formal_preparation import feature_projection
    for ref in release["risk_record_references"]:
        key = ref["run_id"], ref["record_sha256"]
        if key in seen:
            continue
        seen.add(key)
        reader, (risks, checkpoints) = readers[key[0]], indices[key[0]]
        row = risks[key[1]]
        if (row["task_id"] != ref["task_id"] or row["run_id"] != key[0] or row["split"] != "train"
                or row["formal_experiment"] is not True or row["offline_fixture"] is not False
                or row["future_data_excluded"] is not True or type(row["label"]) is not int or row["label"] not in (0, 1)):
            raise ValueError("Unqualified Train risk record")
        cp = checkpoints[row["checkpoint_record_sha256"]]
        if any(cp[k] != row[k] for k in ("task_id", "template_id", "checkpoint_id", "run_id", "split")):
            raise ValueError("Train risk/Checkpoint identity mismatch")
        path = cp["checkpoint_ref"]
        files = {"agent": path + "/agent/agent_state.json", "budget": path + "/runtime/budget_state.json",
            "position": path + "/runtime/execution_position.json", "features": row["feature_provenance_ref"],
            "models": f"tasks/{row['task_id']}/source_model_requests.json",
            "tools": f"tasks/{row['task_id']}/source_tools.jsonl"}
        payload = reader.read(files["agent"])["payload"]
        if (canonical(payload) != cp["hashes"]["agent_state_hash"]
                or canonical(payload["canonical_next_model_input"]) != cp["hashes"]["next_model_input_hash"]):
            raise ValueError("Captured Agent/next-input hash mismatch")
        if cp.get("step_index", payload["agent"]["step_number"]) != payload["agent"]["step_number"]:
            raise ValueError("Captured step and canonical Checkpoint record disagree")
        budget, position = reader.read(files["budget"]), reader.read(files["position"])
        if position != {"next_operation": "model_generation", "phase": "after_tool_observation_before_model_generation"}:
            raise ValueError("Not a nonterminal decision boundary")
        models, tools = reader.read(files["models"]), reader.read(files["tools"], lines=True)
        temporal = validate_cutoff(payload, budget, models, tools)
        packet = reader.read(files["features"])
        runtime_budget = {**budget, "tokens_used": budget["prompt_tokens_used"] + budget["completion_tokens_used"]}
        recomputed = extract_decision_features(agent_payload=payload, budget=runtime_budget,
            source_model_records=models, source_tool_records=tools)
        values = feature_projection(row)
        if (tuple(packet["feature_names"]) != NAMES or packet["values"] != values
                or recomputed["values"] != values or canonical(packet["provenance"]) != row["provenance_sha256"]
                or {k: v for k, v in recomputed["provenance"].items() if k != "created_at"}
                != {k: v for k, v in packet["provenance"].items() if k != "created_at"}):
            raise ValueError("Formal feature/provenance recomputation mismatch")
        selected.append({"task_id": row["task_id"], "template_id": row["template_id"],
            "checkpoint_id": row["checkpoint_id"], "y_failure": 1 - row["label"], "features": values,
            "payload": payload, "budget": budget, "models": models, "tools": tools, "temporal": temporal,
            "checkpoint_record_sha256": cp["record_sha256"], "risk_record_sha256": row["record_sha256"],
            "sources": {role: {"path": str((reader.base / p).relative_to(root)), "sha256": reader.used[p]}
                        for role, p in files.items()}})
    if len({r["task_id"] for r in selected}) != len(selected) or len({r["checkpoint_id"] for r in selected}) != len(selected):
        raise ValueError("Duplicate primary task or Checkpoint across Train runs")
    return selected, {"train_proof_sha256": proof_sha256, "train_release_sha256": proof["release_sha256"],
        "censored_task_ids": release["censored_task_ids"], "manifest_entries_indexed": sum(len(r.index) for r in readers.values()),
        "used_files_verified": sum(len(r.used) for r in readers.values()),
        "verified_files": {str((r.base / n).relative_to(root)): h for r in readers.values() for n, h in r.used.items()},
        "source_manifests": {str(r.manifest_path.relative_to(root)): r.manifest_sha256 for r in readers.values()},
        "validation_or_test_loaded": False, "future_outcomes_loaded": False, "hidden_fault_state_loaded": False,
        "label_source": "published Train risk_candidates labels only; no continue/verifier file opened"}
