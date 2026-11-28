#!/usr/bin/env python3
"""Qualified continuation of an immutable Train parent; no retries or replacements."""
from __future__ import annotations

import argparse,json,shutil,sys,uuid
from pathlib import Path

from risk_agent.collection.causal_collection import CAUSAL_MANIFEST,load_causal_contract
from risk_agent.collection.train_continuation import TrainContinuationCollector,audit_train_union
from risk_agent.collection.formal_contract import write_once_json
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.collection.formal_readiness import verify_engineering_gate
from risk_agent.collection.continue_acceptance_v2 import load_entry
try:
    from scripts.run_frozen_continue_acceptance import _offline_guard,validate_fresh_remote_evidence
    from scripts.run_p2_d2_real_qwen_gate import _api_preflight
except ModuleNotFoundError:
    from run_frozen_continue_acceptance import _offline_guard,validate_fresh_remote_evidence
    from run_p2_d2_real_qwen_gate import _api_preflight


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument("--mode",choices=("offline-gate","train"),default="offline-gate")
    p.add_argument("--parent-run",type=Path,required=True);p.add_argument("--parent-manifest-sha256",required=True)
    p.add_argument("--run-id",required=True);p.add_argument("--output-root",type=Path,default=Path("runs/phase2_formal_train_continuation"))
    p.add_argument("--authorize-remaining-train",action="store_true")
    for name in("remote-evidence","connection-spec","acceptance-receipt","engineering-gate-run"):
        p.add_argument("--"+name,type=Path)
    args=p.parse_args(argv);root=args.root.resolve();live=args.mode=="train";load_causal_contract(root)
    if not live:_offline_guard()
    else:
        if not args.authorize_remaining_train or not all((args.remote_evidence,args.connection_spec,args.acceptance_receipt,args.engineering_gate_run)):
            raise ValueError("Remaining Train requires prior authorization, local qualification and fresh remote proof")
        receipt=json.loads(args.acceptance_receipt.read_text())
        if (receipt.get("passed")is not True or receipt.get("qualification_only")is not True
                or receipt.get("causal_manifest_sha256")!=sha256_file(root/CAUSAL_MANIFEST)
                or receipt.get("parent_artifact_manifest_sha256")!=args.parent_manifest_sha256):
            raise ValueError("Continuation qualification receipt mismatch")
        profile,entry=load_entry(root)
        proof=verify_engineering_gate(args.engineering_gate_run,profile.manifest_sha256,entry["task_ids"])
        if not proof["verified"]:raise ValueError("Historical real engineering Gate is not verified")
        validate_fresh_remote_evidence(json.loads(args.remote_evidence.read_text()),args.run_id,entry)
    kwargs={};factory=None
    if not live:
        from risk_agent.collection.formal_fixture import FormalOfflineFactory
        factory=FormalOfflineFactory(load_entry(root)[0].tasks);kwargs["agent_factory"]=factory
    collector=TrainContinuationCollector(root=root,parent_run=args.parent_run,parent_manifest_sha256=args.parent_manifest_sha256,
        run_directory=root/args.output_root/args.run_id,run_id=args.run_id,offline_fixture=not live,
        authorize_formal_train=args.authorize_remaining_train,**kwargs)
    session=collector.directory/"connections"/uuid.uuid4().hex[:10];session.mkdir(parents=True)
    connection=None;release={"released":True,"owned_tunnels":0};summary={"passed":False,"gate":"TRAIN_CONTINUATION_BLOCKED"}
    try:
        if shutil.disk_usage(collector.directory).free<20*1024**3:raise RuntimeError("Insufficient free disk")
        if live:
            collector.settings.resolve_api_key();collector.settings.resolve_ca_bundle()
            if receipt.get("continuation_scope_sha256")!=collector.scope_sha:
                raise ValueError("Qualified remaining task scope mismatch")
            # Persistent exclusive claim prevents a new child ID from silently
            # repeating the same remaining Source attempts after an interruption.
            control=root/"runs/phase2_formal_continuation_control"/collector.continuation["parent_run_id"]
            write_once_json(control/"launch_claim.json",{"child_run_id":args.run_id,
                "scope_sha256":collector.scope_sha,"causal_manifest_sha256":sha256_file(root/CAUSAL_MANIFEST)})
            from risk_agent.monitoring.connection_lifecycle import ConnectionLifecycle
            connection=ConnectionLifecycle(args.run_id,session/"connection_audit.jsonl")
            collector.audit.connection_observer=connection
            connection.launch(json.loads(args.connection_spec.read_text()))
            write_once_json(session/"api_preflight.json",_api_preflight(collector.settings))
            write_once_json(session/"evidence_binding.json",{"remote_evidence_sha256":sha256_file(args.remote_evidence),
                "acceptance_receipt_sha256":sha256_file(args.acceptance_receipt)})
        summary=collector.run_train();summary["passed"]=summary["complete"]and not summary["batch_stopped"]
    except (Exception,KeyboardInterrupt)as exc:
        from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
        summary.update(passed=False,gate="TRAIN_CONTINUATION_BLOCKED",error_type=type(exc).__name__,
            error_message=sanitize_audit_value(str(exc)))
    finally:
        if connection:
            release["owned_tunnels"]=len(connection.adapters)
            try:connection.close()
            except Exception as exc:release.update(released=False,error_type=type(exc).__name__)
        write_once_json(session/"resource_release.json",release)
        summary["connection_resources_released"]=release["released"]
        if not release["released"]:summary["passed"]=False
        if summary.get("complete")and release["released"]:
            try:
                union=audit_train_union(collector,summary,release=live)
                summary.update(union_quality_passed=union["quality_passed"],union_gate=union["gate"])
                summary["passed"]=bool(summary["passed"]and union["quality_passed"])
            except Exception as exc:summary.update(passed=False,union_error_type=type(exc).__name__)
        write_once_json(collector.directory/"final_summary.json",summary)
        if factory:write_once_json(session/"offline_fixture_requests.json",factory.requests)
        collector.close()
        write_once_json(collector.directory/"artifact_manifest.json",{"files":[
            {"path":str(f.relative_to(collector.directory)),"size_bytes":f.stat().st_size,"sha256":sha256_file(f)}
            for f in sorted(collector.directory.rglob('*'))if f.is_file()]})
    print(json.dumps(summary,indent=2,ensure_ascii=False));return 0 if summary["passed"]else 1


if __name__=="__main__":sys.exit(main())
