"""v2.2 engineering acceptance; valid negative outcomes are first-class results."""

from __future__ import annotations

import json
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from risk_agent.agent.agent_factory import create_checkpointable_benchmark_agent
from risk_agent.agent.worker_router import RemoteModelSettings
from risk_agent.agent.agent_state_adapter import canonical_hash
from risk_agent.benchmark.schema import DataSplit, validate_identifier
from risk_agent.checkpoint import CheckpointManager
from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
from .continue_acceptance import (
    ContinueAcceptanceRunner as LegacyAcceptanceRunner, AcceptanceProviderAudit,
    FLAGS, read_jsonl, write_json,
)
from .continue_gate import ContinueGateRunner, ContinueGateError, _exception_chain
from .continue_outcome import ContinueTermination
from .day5_gate import sha256_file, canonical_task_catalog_hash, split_manifest_hash
from .output_contract import LosslessAgentFactory, OutputContractError
from .protocol_freeze_v2_2 import MANIFEST_PATH, load_frozen_collection

CONFIG_PATH = "configs/continue_acceptance_v2_2.yaml"
ENTRY_MANIFEST_PATH = MANIFEST_PATH
HASH_KEYS = {"agent_state_hash", "next_model_input_hash", "environment_state_hash",
             "runtime_state_hash", "identity_config_hash", "combined_state_hash"}


def load_entry(root: Path):
    root = Path(root).resolve()
    profile = load_frozen_collection(root)
    config = yaml.safe_load((root / CONFIG_PATH).read_text())
    tasks = {t.task_id:t for t in profile.tasks}
    if (config["protocol_id"] != profile.config["protocol_id"]
        or config["entry_version"] != "frozen-continue-acceptance-v2"
        or len(config["task_ids"]) != 5 or len(set(config["task_ids"])) != 5
        or any(tasks[t].split is not DataSplit.TRAIN for t in config["task_ids"])):
        raise ValueError("v2.2 requires its five pinned distinct Train tasks")
    return profile, config


def engineering_passed(row: dict[str, Any]) -> bool:
    c = row.get("component", {})
    if not isinstance(c, dict) or not isinstance(row.get("provider_audit"), dict):
        return False
    y = row.get("accepted_y_continue")
    hashes = c.get("restore_hashes", {})
    if not isinstance(hashes, dict):
        return False
    return bool(type(y) is int and y in (0, 1)
        and type(c.get("y_continue")) is int and c["y_continue"] == y
        and c.get("verifier_passed") is (y == 1)
        and c.get("verifier_outcome_id") and c.get("continue_outcome_id")
        and c.get("complete_chain") is True
        and set(hashes) == HASH_KEYS and all(v is True for v in hashes.values())
        and c.get("first_input_exact") is True and c.get("checkpoint_immutable") is True
        and c.get("tool_lineage_complete") is True and c.get("tool_replay_detected") is False
        and row.get("source_unchanged") is True
        and row.get("provider_audit", {}).get("complete") is True
        and row.get("resources_released") is True and not row.get("error_type"))


class OutputContractContinueGateRunner(ContinueGateRunner):
    def _validate_identity(self, verified: Any, worker: Any) -> None:
        super()._validate_identity(verified, worker)
        identity = verified.manifest.identity
        if (identity["catalog_hash"] != canonical_task_catalog_hash(tuple(self.tasks.values()))
            or identity["split_manifest_hash"] != split_manifest_hash(tuple(self.tasks.values()))):
            raise ContinueGateError("v2.2 catalog/split differs from Checkpoint")

    @staticmethod
    def _termination_for_exception(exc: BaseException, model: Any) -> ContinueTermination:
        if any(isinstance(e, OutputContractError) for e in _exception_chain(exc)):
            return ContinueTermination.ENVIRONMENT_FAILURE
        return ContinueGateRunner._termination_for_exception(exc, model)


class ContinueAcceptanceRunner(LegacyAcceptanceRunner):
    # Reuse Source capture, shared budgets, Provider Audit and model factory wiring.
    # Initialization and outcome policy are versioned; no legacy globals are patched.
    def __init__(self, *, root: str | Path, run_directory: str | Path, run_id: str,
                 offline_fixture: bool = False,
                 authorize_remote: bool = False,
                 agent_factory: Callable[..., Any] = create_checkpointable_benchmark_agent,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.root = Path(root).resolve()
        self.profile, self.config = load_entry(self.root)
        if not offline_fixture and not authorize_remote:
            raise ValueError("Real execution requires explicit authorize_remote")
        if not offline_fixture and agent_factory is not create_checkpointable_benchmark_agent:
            raise ValueError("Injected backends must be explicitly marked offline_fixture")
        self.run_id = validate_identifier(run_id, "run_id")
        self.directory = Path(run_directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.offline_fixture = offline_fixture
        self.base_factory = LosslessAgentFactory(agent_factory)
        self.clock = clock
        self.settings = RemoteModelSettings.from_yaml(
            self.root / self.profile.config["dependencies"]["remote_model_config"])
        self.tasks = {task.task_id: task for task in self.profile.tasks}
        self.workers = {worker.worker_id: worker for worker in self.settings.workers}
        self.audit = AcceptanceProviderAudit(self.directory / "audit")
        self.manager = CheckpointManager(self.directory / "source/checkpoints")
        self.attempted: set[str] = set()
        manifest_bytes = (self.root / ENTRY_MANIFEST_PATH).read_bytes()
        with (self.directory / "entry_manifest_snapshot.json").open("xb") as handle:
            handle.write(manifest_bytes)
        for name in json.loads(manifest_bytes)["files"]:
            target = self.directory / "entry_inputs" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write((self.root / name).read_bytes())
        write_json(self.directory / "run_manifest.json", {
            "run_id": run_id, "protocol_id": self.profile.config["protocol_id"],
            "implementation_revision": self.profile.config["implementation_revision"],
            "freeze_manifest_sha256": self.profile.manifest_sha256,
            "entry_manifest_sha256": sha256_file(self.root / ENTRY_MANIFEST_PATH),
            "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip(),
            "git_worktree_dirty": bool(subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=self.root, text=True).strip()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "offline_fixture": offline_fixture, "scope": self.config,
            "model_id": self.settings.model_id, "inference": self.settings.inference,
            "catalog_sha256": self.profile.audit_summary()["catalog_sha256"],
            "split_sha256": self.profile.audit_summary()["split_manifest_sha256"], **FLAGS,
        })

    def run_task(self, task_id: str, worker_id: str) -> dict[str, Any]:
        if task_id not in self.config["task_ids"] or task_id in self.attempted:
            raise ValueError("Unselected or already attempted task; retries/replacements forbidden")
        expected = self.config["worker_schedule"][self.config["task_ids"].index(task_id)]
        if worker_id != expected:
            raise ValueError("Frozen worker assignment differs; failover forbidden")
        load_entry(self.root)
        self.attempted.add(task_id)
        task, worker = self.tasks[task_id], self.workers[worker_id]
        task_root = self.directory / "tasks" / task_id
        task_root.mkdir(parents=True, exist_ok=False)
        started = self.clock()
        clients: list[Any] = []
        result = {"task_id": task_id, "worker_id": worker_id, "accepted_y_continue": None,
                  "passed": False, "offline_fixture": self.offline_fixture, **FLAGS}
        try:
            record, environment, runtime, model = self._capture(task, worker, task_root, started, clients)
            source_hash = environment.state_hash()
            source_runtime_hash = canonical_hash(runtime.to_dict())
            source_calls = model.executed_calls
            continuation = OutputContractContinueGateRunner(
                settings=self.settings, tasks=self.profile.tasks,
                replay_policy=self.profile.config["continue"]["replay_policy"],
                protocol_id=self.profile.config["protocol_id"], run_directory=self.directory / "continue", checkpoint_run=self.directory / "source",
                run_id=self.run_id, agent_factory=self._factory(task, "continue", started, clients), clock=self.clock)
            component = continuation.run_checkpoint(audit=record, worker=worker, branch_attempt_index=1)
            unchanged = (environment.state_hash() == source_hash and canonical_hash(runtime.to_dict()) == source_runtime_hash
                         and model.executed_calls == source_calls)
            audit = self._audit_complete(task_id)
            audit["complete"] = bool(audit["complete"] and audit["source_events"] == source_calls
                and audit["continue_events"] == component["model_requests_added"])
            eligible = component["complete_chain"] and unchanged and audit["complete"]
            result.update(checkpoint_id=record.checkpoint_id, capture_step=record.step_number,
                source_model_calls=source_calls, source_unchanged=unchanged,
                component=component, provider_audit=audit,
                accepted_y_continue=component["y_continue"] if eligible else None,
                wall_seconds=self.clock() - started)
        except (Exception, KeyboardInterrupt) as exc:
            result.update(error_type=type(exc).__name__, exception_chain=sanitize_audit_value(
                "".join(traceback.format_exception(exc))))
        finally:
            errors = []
            for phase, client in clients:
                try:
                    client.close()
                    closed = client.is_closed() if callable(client.is_closed) else client.is_closed
                    if not closed:
                        raise RuntimeError("Owned client did not close")
                except Exception as exc:
                    errors.append({"phase": phase, "error_type": type(exc).__name__})
            abandoned = 0
            if not errors:
                try:
                    abandoned = self.audit.release_aborted(task_id)
                except Exception as exc:
                    errors.append({"phase": "request_release", "error_type": type(exc).__name__})
            release = {"owned_clients": len(clients), "close_errors": errors,
                       "aborted_requests_released_after_client_close": abandoned,
                       "all_closed": not errors, "external_processes_terminated": 0}
            write_json(task_root / "resource_release.json", release)
            result["resources_released"] = release["all_closed"]
            if errors:
                result.update(passed=False, accepted_y_continue=None)
        result.update(engineering_passed=engineering_passed(result))
        result["passed"] = result["engineering_passed"]
        if not result["engineering_passed"]:
            result["accepted_y_continue"] = None
        result["task_success"] = (result["accepted_y_continue"] == 1
                                  if result["accepted_y_continue"] is not None else None)
        write_json(task_root / "task_result.json", result)
        return result

    def run_fixed_gate(self) -> dict[str, Any]:
        rows = []
        for task_id, worker_id in zip(self.config["task_ids"], self.config["worker_schedule"], strict=True):
            rows.append(self.run_task(task_id, worker_id))
            if not rows[-1]["engineering_passed"]:
                break
        passed = len(rows) == 5 and all(r["engineering_passed"] for r in rows)
        successes = sum(r["accepted_y_continue"] == 1 for r in rows)
        failures = sum(r["accepted_y_continue"] == 0 for r in rows)
        summary = {"run_id": self.run_id, "entry_version": self.config["entry_version"],
            "protocol_id": self.profile.config["protocol_id"],
            "passed": passed, "engineering_passed": passed,
            "gate": ("CONTINUE_ENTRY_V2_OFFLINE_PASS" if self.offline_fixture else "CONTINUE_ENGINEERING_GATE_PASS")
                    if passed else "CONTINUE_ENGINEERING_GATE_BLOCKED",
            "offline_fixture": self.offline_fixture, "tasks": rows, "task_attempts": len(rows),
            "successes": successes, "valid_failures": failures,
            "censored_or_invalid": sum(r["accepted_y_continue"] is None for r in rows),
            "not_attempted": 5 - len(rows),
            "task_success_rate_among_valid": successes / (successes + failures) if successes + failures else None,
            "automatic_retries": 0, "worker_failover": False,
            "real_model_calls": 0 if self.offline_fixture else len(read_jsonl(self.audit.response_path)),
            "formal_collection_ready": False, **FLAGS}
        write_json(self.directory / "gate_summary.json", summary)
        return summary
