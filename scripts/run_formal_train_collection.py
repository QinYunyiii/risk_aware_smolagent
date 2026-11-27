#!/usr/bin/env python3
"""Frozen formal Train only. Local modes never contact the remote service."""
from __future__ import annotations
import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

from risk_agent.collection.formal_collection import FormalCollector
from risk_agent.collection.formal_contract import MANIFEST, load_formal_contract, write_once_json as write_json
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.collection.formal_readiness import verify_engineering_gate
from risk_agent.collection.continue_acceptance_v2 import load_entry
try:
    from scripts.run_frozen_continue_acceptance import _offline_guard, validate_fresh_remote_evidence
    from scripts.run_p2_d2_real_qwen_gate import _api_preflight
except ModuleNotFoundError:
    from run_frozen_continue_acceptance import _offline_guard, validate_fresh_remote_evidence
    from run_p2_d2_real_qwen_gate import _api_preflight


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",choices=("local-preflight","offline-gate","train"),default="local-preflight")
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-root",type=Path,default=Path("runs/phase2_formal_train"))
    parser.add_argument("--run-id",required=True)
    parser.add_argument("--authorize-formal-train",action="store_true")
    parser.add_argument("--resume",action="store_true")
    parser.add_argument("--remote-evidence",type=Path)
    parser.add_argument("--connection-spec",type=Path)
    parser.add_argument("--acceptance-receipt",type=Path)
    parser.add_argument("--engineering-gate-run",type=Path)
    args=parser.parse_args(argv);root=args.root.resolve();live=args.mode=="train"
    profile,plan,contract=load_formal_contract(root)
    if live:
        if not args.authorize_formal_train or not all((args.remote_evidence,args.connection_spec,args.acceptance_receipt,args.engineering_gate_run)):
            raise ValueError("Formal Train requires authorization, fresh remote proof, local acceptance and actual real engineering Gate")
        entry=load_entry(root)[1]
        proof=verify_engineering_gate(args.engineering_gate_run,profile.manifest_sha256,entry["task_ids"])
        receipt=json.loads(args.acceptance_receipt.read_text())
        if (not proof["verified"] or receipt.get("passed") is not True
            or receipt.get("formal_manifest_sha256")!=sha256_file(root/MANIFEST)
            or receipt.get("qualification_only") is not True):
            raise ValueError("Formal local/real qualification proof mismatch")
        evidence=json.loads(args.remote_evidence.read_text())
        validate_fresh_remote_evidence(evidence,args.run_id,entry)
        spec=json.loads(args.connection_spec.read_text())
    else:
        _offline_guard()
    factory=None
    if args.mode=="offline-gate":
        from risk_agent.collection.formal_fixture import FormalOfflineFactory
        factory=FormalOfflineFactory(profile.tasks)
    kwargs={"agent_factory":factory} if factory else {}
    collector=FormalCollector(root=root,run_directory=root/args.output_root/args.run_id,run_id=args.run_id,
        offline_fixture=not live,authorize_formal_train=args.authorize_formal_train,resume=args.resume,**kwargs)
    epoch=uuid.uuid4().hex[:10];session=collector.directory/"connections"/epoch;session.mkdir(parents=True)
    connections=None;release={"released":True,"owned_tunnels":0};summary={"gate":"FORMAL_TRAIN_BLOCKED","passed":False}
    try:
        if shutil.disk_usage(collector.directory).free<20*1024**3:raise RuntimeError("Disk free space below frozen preflight threshold")
        if args.mode=="local-preflight":
            summary={"gate":"FORMAL_LOCAL_PREFLIGHT_PASS","passed":True,"model_requests":0,**collector.flags}
        else:
            if live:
                collector.settings.resolve_api_key();collector.settings.resolve_ca_bundle()
                from risk_agent.monitoring.connection_lifecycle import ConnectionLifecycle
                connections=ConnectionLifecycle(args.run_id,session/"connection_audit.jsonl")
                collector.audit.connection_observer=connections
                connections.launch(spec)
                release["owned_tunnels"]=len(connections.adapters)
                write_json(session/"api_preflight.json",_api_preflight(collector.settings))
                write_json(session/"evidence_binding.json",{"remote_evidence_sha256":sha256_file(args.remote_evidence),
                    "acceptance_receipt_sha256":sha256_file(args.acceptance_receipt),"engineering_gate_run_id":proof["run_id"]})
            summary=collector.run_train()
            summary["passed"]=bool(summary["complete"] and not summary["batch_stopped"])
    except (Exception,KeyboardInterrupt) as exc:
        from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
        summary.update(gate="FORMAL_TRAIN_BLOCKED",passed=False,error_type=type(exc).__name__,
            error_message=sanitize_audit_value(str(exc)))
    finally:
        if connections:
            release["owned_tunnels"]=len(connections.adapters)
            try:connections.close()
            except Exception as exc:
                release.update(released=False,error_type=type(exc).__name__)
                summary.update(passed=False,gate="FORMAL_TRAIN_BLOCKED")
        write_json(session/"resource_release.json",release)
        summary.update(connection_resources_released=release["released"],epoch=epoch)
        write_json(collector.directory/f"final_summary_{epoch}.json",summary)
        if factory:write_json(session/"offline_fixture_requests.json",factory.requests)
        write_json(collector.directory/f"artifact_manifest_{epoch}.json",{"run_id":args.run_id,"files":[
            {"path":str(p.relative_to(collector.directory)),"size_bytes":p.stat().st_size,"sha256":sha256_file(p)}
            for p in sorted(collector.directory.rglob("*")) if p.is_file()]})
        collector.close()
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0 if summary["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
