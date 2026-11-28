"""Read-only verification of published Train eligibility before Validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .day5_gate import sha256_file
from .formal_contract import read_records,load_formal_contract


def verify_train_prerequisite(root:Path,proof_path:Path)->dict[str,Any]:
    root=root.resolve();proof=json.loads(proof_path.read_text())
    profile,_,_=load_formal_contract(root)
    train_ids={t.task_id for t in profile.tasks if t.split.value=="train"}
    sources={};indexed={};tasks=[];risks={};verified=0
    for source in proof["runs"]:
        directory=Path(source["directory"]).resolve()
        if not directory.is_relative_to(root/"runs"):raise ValueError("Train proof outside owned run storage")
        identity_path=directory/"run_manifest.json"
        identity=json.loads(identity_path.read_text())
        # Reject other splits/modes before opening their outcomes or other files.
        if (identity["authorized_split"]!="train"or identity["formal_experiment"]is not True
                or identity["offline_fixture"]is not False or identity["run_id"]!=source["run_id"]):
            raise ValueError("Validation prerequisite must contain actual formal Train runs only")
        if source["run_id"]in sources:raise ValueError("Duplicate prerequisite Run ID")
        manifest=(directory/source["artifact_manifest"]).resolve()
        if not manifest.is_relative_to(directory)or sha256_file(manifest)!=source["artifact_manifest_sha256"]:
            raise ValueError("Train Artifact Manifest differs from trusted digest")
        entries=json.loads(manifest.read_text())["files"];names=set()
        for item in entries:
            path=(directory/item["path"]).resolve()
            if (item["path"]in names or not path.is_relative_to(directory)
                    or path.stat().st_size!=item["size_bytes"]or sha256_file(path)!=item["sha256"]):
                raise ValueError("Train prerequisite artifact drift")
            names.add(item["path"]);indexed[path]=item["sha256"];verified+=1
        required={"run_manifest.json","records/tasks.jsonl","records/risk_candidates.jsonl","records/continues.jsonl","records/checkpoints.jsonl"}
        if not required<=names:raise ValueError("Train manifest omits prerequisite records")
        rows=read_records(directory/"records/risk_candidates.jsonl")
        outcomes={r["record_sha256"]:r for r in read_records(directory/"records/continues.jsonl")}
        checkpoints={r["record_sha256"]:r for r in read_records(directory/"records/checkpoints.jsonl")}
        for row in rows:
            if (row["split"]!="train"or row["formal_experiment"]is not True or row["offline_fixture"]is not False
                    or row["task_id"]not in train_ids or type(row["label"])is not int or row["label"]not in(0,1)
                    or row["outcome_record_sha256"]not in outcomes or row["checkpoint_record_sha256"]not in checkpoints):
                raise ValueError("Invalid or non-Train risk prerequisite")
            key=(source["run_id"],row["record_sha256"])
            if key in risks:raise ValueError("Duplicate Train risk record")
            risks[key]=row
        tasks+=read_records(directory/"records/tasks.jsonl");sources[source["run_id"]]=directory
    task_ids=[r["task_id"]for r in tasks]
    if len(task_ids)!=96 or len(set(task_ids))!=96 or set(task_ids)!=train_ids:
        raise ValueError("Train prerequisite does not cover the completed 96-attempt cohort")
    release_path=Path(proof["release_path"]).resolve()
    if release_path not in indexed or indexed[release_path]!=proof["release_sha256"]:
        raise ValueError("Train eligibility release is not manifest-bound")
    release=json.loads(release_path.read_text())
    if release["split"]!="train"or release["formal_experiment"]is not True or release["research_dataset_eligible"]is not True:
        raise ValueError("Train eligibility release is not qualified")
    refs=release["risk_record_references"];selected=[]
    for ref in refs:
        row=risks.get((ref["run_id"],ref["record_sha256"]))
        if row is None or row["task_id"]!=ref["task_id"]:raise ValueError("Train eligibility reference mismatch")
        selected.append(row)
    ids=[r["task_id"]for r in selected]
    if len(ids)<72 or len(set(ids))!=len(ids) or {r["label"]for r in selected}!={0,1}:
        raise ValueError("Train eligibility count/class/uniqueness gate failed")
    censored=set(release["censored_task_ids"])
    if set(ids)|censored!=train_ids or set(ids)&censored:raise ValueError("Train censoring lineage mismatch")
    return {"verified":True,"proof_sha256":sha256_file(proof_path),"train_release_sha256":proof["release_sha256"],
        "train_attempts":96,"train_eligible_rows":len(selected),"censored_task_ids":sorted(censored),
        "artifacts_verified":verified,"validation_or_test_outcomes_read":False,"train_data_imported_into_validation":False}
