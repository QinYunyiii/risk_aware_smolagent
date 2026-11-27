#!/usr/bin/env python3
"""Offline qualification and explicit continuation review; no live execution mode."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from risk_agent.collection.day5_gate import sha256_file
from risk_agent.collection.formal_budget_revision import (
    BudgetAuditFormalCollector, REVISION_MANIFEST, load_revision, prepare_continuation_plan)
from risk_agent.collection.formal_contract import load_formal_contract, write_once_json


def main(argv=None):
    try:
        from scripts.run_frozen_continue_acceptance import _offline_guard
    except ModuleNotFoundError:
        from run_frozen_continue_acceptance import _offline_guard
    _offline_guard()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--source-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--offline-gate", action="store_true")
    args = parser.parse_args(argv)
    from risk_agent.benchmark.schema import validate_identifier
    validate_identifier(args.run_id, "run_id")
    root = args.root.resolve()
    load_revision(root)
    plan = prepare_continuation_plan(root=root, source_run=args.source_run,
        expected_manifest_sha256=args.source_manifest_sha256)
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_once_json(args.output / "continuation_plan.json", plan)
    summary = None
    if args.offline_gate:
        from risk_agent.collection.formal_fixture import FormalOfflineFactory
        profile, _, _ = load_formal_contract(root)
        factory = FormalOfflineFactory(profile.tasks)
        collector = BudgetAuditFormalCollector(root=root, run_directory=args.output / "offline_gate",
            run_id=args.run_id, offline_fixture=True, agent_factory=factory)
        try:
            summary = collector.run_train()
        finally:
            collector.close()
        write_once_json(args.output / "offline_fixture_requests.json", factory.requests)
        write_once_json(args.output / "offline_gate_summary.json", summary)
    passed = summary is None or bool(summary["complete"] and summary["quality_passed"])
    receipt = {"gate": "BUDGET_AUDIT_OFFLINE_PASS" if passed else "BUDGET_AUDIT_OFFLINE_BLOCKED",
        "passed": passed, "qualification_only": True, "execution_authorized": False,
        "run_id": args.run_id, "implementation_manifest_sha256": sha256_file(root / REVISION_MANIFEST),
        "continuation_plan_sha256": sha256_file(args.output / "continuation_plan.json"),
        "remaining_primary": plan["remaining_primary"],
        "remaining_repeats": len(plan["remaining_repeat_task_ids"]),
        "offline_gate_executed": summary is not None, "network_requests": 0,
        "formal_experiment": False, "research_dataset_eligible": False}
    write_once_json(args.output / "qualification_receipt.json", receipt)
    write_once_json(args.output / "artifact_manifest.json", {"files": [
        {"path": str(p.relative_to(args.output)), "size_bytes": p.stat().st_size, "sha256": sha256_file(p)}
        for p in sorted(args.output.rglob("*")) if p.is_file()]})
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
