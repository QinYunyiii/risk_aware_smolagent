#!/usr/bin/env python3
"""Aggregate-family Validation entry. Default is offline; a new cohort needs separate authorization."""
from __future__ import annotations

import argparse,json,shutil,subprocess,sys,uuid
from pathlib import Path

from risk_agent.collection.aggregate_validation import AggregateValidationCollector
from risk_agent.collection.validation_contract import VALIDATION_MANIFEST
from risk_agent.collection.aggregate_target_contract import TARGET_MANIFEST,TARGET_CONFIG,load_aggregate_contract,claim_aggregate_cohort
from risk_agent.collection.validation_prerequisite import verify_train_prerequisite
from risk_agent.collection.formal_contract import write_once_json
from risk_agent.collection.day5_gate import sha256_file
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
    p.add_argument("--mode",choices=("local-preflight","offline-gate","validation"),default="local-preflight")
    p.add_argument("--run-id",required=True);p.add_argument("--output-root",type=Path,default=Path("runs/phase2_formal_validation_aggregate_v1"))
    p.add_argument("--authorize-aggregate-validation",action="store_true")
    for name in("train-proof","remote-evidence","connection-spec","acceptance-receipt"):
        p.add_argument("--"+name,type=Path)
    args=p.parse_args(argv);root=args.root.resolve();live=args.mode=="validation"
    profile,plan,_,config,_,_=load_aggregate_contract(root)
    if not live:_offline_guard()
    elif not args.authorize_aggregate_validation or not all((args.train_proof,args.remote_evidence,args.connection_spec,args.acceptance_receipt)):
        raise ValueError("Real Validation requires separate authorization, Train qualification, local receipt and fresh remote proof")
    train=verify_train_prerequisite(root,args.train_proof)if args.train_proof else None
    if live:
        receipt=json.loads(args.acceptance_receipt.read_text())
        if (receipt.get("passed")is not True or receipt.get("qualification_only")is not True
                or receipt.get("offline_gate_executed")is not True
                or receipt.get("aggregate_manifest_sha256")!=sha256_file(root/TARGET_MANIFEST)
                or receipt.get("policy_sha256")!=sha256_file(root/TARGET_CONFIG)
                or receipt.get("validation_manifest_sha256")!=sha256_file(root/VALIDATION_MANIFEST)
                or receipt.get("train_proof_sha256")!=train["proof_sha256"]):
            raise ValueError("Validation local qualification binding mismatch")
        if subprocess.check_output(["git","status","--short"],cwd=root,text=True).strip():
            raise ValueError("Real Validation requires clean committed code")
        validate_fresh_remote_evidence(json.loads(args.remote_evidence.read_text()),args.run_id,load_entry(root)[1])
    factory=None;kwargs={}
    if not live:
        from risk_agent.collection.aggregate_validation_fixture import AggregateValidationOfflineFactory
        factory=AggregateValidationOfflineFactory(profile.tasks,plan);kwargs["agent_factory"]=factory
    collector=AggregateValidationCollector(root=root,run_directory=root/args.output_root/args.run_id,run_id=args.run_id,
        offline_fixture=not live,authorize_aggregate_validation=args.authorize_aggregate_validation,**kwargs)
    session=collector.directory/"connections"/uuid.uuid4().hex[:10];session.mkdir(parents=True)
    connection=None;release={"released":True,"owned_tunnels":0};summary={"passed":False,"gate":"VALIDATION_ENTRY_BLOCKED"}
    try:
        if shutil.disk_usage(collector.directory).free<20*1024**3:raise RuntimeError("Insufficient free disk")
        if train:write_once_json(collector.directory/"train_prerequisite_audit.json",train)
        if live:
            collector.settings.resolve_api_key();collector.settings.resolve_ca_bundle()
            claim_aggregate_cohort(root,args.run_id)
            from risk_agent.monitoring.connection_lifecycle import ConnectionLifecycle
            connection=ConnectionLifecycle(args.run_id,session/"connection_audit.jsonl")
            collector.audit.connection_observer=connection
            connection.launch(json.loads(args.connection_spec.read_text()))
            write_once_json(session/"api_preflight.json",_api_preflight(collector.settings))
            write_once_json(session/"evidence_binding.json",{"remote_evidence_sha256":sha256_file(args.remote_evidence),
                "acceptance_receipt_sha256":sha256_file(args.acceptance_receipt),"train_proof_sha256":train["proof_sha256"]})
        if args.mode=="local-preflight":
            summary={"passed":True,"gate":"VALIDATION_LOCAL_PREFLIGHT_PASS","model_requests":0,**collector.flags}
        else:
            summary=collector.run_validation();summary["passed"]=bool(summary["quality_passed"])
    except (Exception,KeyboardInterrupt)as exc:
        from risk_agent.monitoring.provider_response_audit import sanitize_audit_value
        summary.update(passed=False,gate="VALIDATION_ENTRY_BLOCKED",error_type=type(exc).__name__,
            error_message=sanitize_audit_value(str(exc)))
    finally:
        if connection:
            release["owned_tunnels"]=len(connection.adapters)
            try:connection.close()
            except Exception as exc:release.update(released=False,error_type=type(exc).__name__)
        write_once_json(session/"resource_release.json",release)
        if not release["released"]:summary["passed"]=False
        if live and summary.get("quality_passed")and release["released"]:
            try:collector.publish_validation_eligibility(summary,resources_released=True)
            except Exception as exc:summary.update(passed=False,eligibility_error_type=type(exc).__name__)
        summary["connection_resources_released"]=release["released"]
        write_once_json(collector.directory/"final_summary.json",summary)
        if factory:write_once_json(session/"offline_fixture_requests.json",factory.requests)
        if not live:
            write_once_json(collector.directory/"qualification_receipt.json",{"passed":summary["passed"],"qualification_only":True,
                "offline_gate_executed":args.mode=="offline-gate","execution_authorized":False,
                "validation_manifest_sha256":sha256_file(root/VALIDATION_MANIFEST),
                "aggregate_manifest_sha256":sha256_file(root/TARGET_MANIFEST),
                "policy_sha256":sha256_file(root/TARGET_CONFIG),
                "train_proof_sha256":train["proof_sha256"]if train else None,"real_model_requests":0})
        collector.close()
        write_once_json(collector.directory/"artifact_manifest.json",{"files":[
            {"path":str(f.relative_to(collector.directory)),"size_bytes":f.stat().st_size,"sha256":sha256_file(f)}
            for f in sorted(collector.directory.rglob('*'))if f.is_file()]})
    print(json.dumps(summary,indent=2,ensure_ascii=False));return 0 if summary["passed"]else 1


if __name__=="__main__":sys.exit(main())
