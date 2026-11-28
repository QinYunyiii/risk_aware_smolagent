"""Independent audit revision; research values and frozen parent files unchanged."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .decision_order import DecisionOrderLedger
from .formal_audit import FormalProviderAudit
from .formal_budget_revision import BudgetAuditFormalCollector, REVISION_MANIFEST, load_revision
from .formal_contract import write_once_json, read_records
from .continue_acceptance import read_jsonl
from .day5_gate import sha256_file

CAUSAL_MANIFEST="docs/phase2/decision_order_manifest_v1.json"


def load_causal_contract(root: Path) -> dict[str,Any]:
    root=root.resolve();load_revision(root)
    value=json.loads((root/CAUSAL_MANIFEST).read_text())
    if value["parent_budget_manifest_sha256"]!=sha256_file(root/REVISION_MANIFEST):
        raise ValueError("Causal revision parent changed")
    for name,digest in value["files"].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root)or sha256_file(path)!=digest:raise ValueError(f"Causal input changed: {name}")
    return value


class CausalProviderAudit(FormalProviderAudit):
    def __init__(self,*args,ledger:DecisionOrderLedger,**kwargs):
        self.ledger=ledger
        super().__init__(*args,**kwargs)

    def request_started(self,context,request_id):
        self.ledger.request_started(context,request_id)
        super().request_started(context,request_id)


class CausalFormalCollector(BudgetAuditFormalCollector):
    def __init__(self,**kwargs):
        root=Path(kwargs["root"]).resolve();contract=load_causal_contract(root)
        directory=Path(kwargs["run_directory"]).resolve();digest=sha256_file(root/CAUSAL_MANIFEST)
        if kwargs.get("resume"):
            path=directory/"causal_binding.json"
            if not path.is_file()or json.loads(path.read_text())["causal_manifest_sha256"]!=digest:
                raise ValueError("Cannot resume an older causal implementation")
        super().__init__(**kwargs)
        try:
            self.flags.update(causal_revision="decision-order-v1",causal_manifest_sha256=digest)
            self.order=DecisionOrderLedger(self.directory,run_id=self.run_id,experiment_id=self.experiment_id)
            self.audit=CausalProviderAudit(self.directory/"audit",ledger=self.order,flags=self.flags,
                experiment_id=self.experiment_id,tasks=self.tasks)
            if not kwargs.get("resume"):
                write_once_json(directory/"causal_binding.json",{"run_id":self.run_id,
                    "causal_manifest_sha256":digest,"budget_binding_sha256":sha256_file(directory/"implementation_binding.json"),
                    "order_schema":"decision-order-v1","wall_clock_policy":"raw_timestamp_only",
                    "causal_order_policy":"hash_chained_sequence_and_same_process_monotonic"})
                for name in {CAUSAL_MANIFEST,*contract["files"]}:
                    p=directory/"frozen_causal_inputs"/name;p.parent.mkdir(parents=True,exist_ok=True)
                    with p.open("xb")as f:f.write((root/name).read_bytes())
        except BaseException:
            self.close();raise

    def _packet(self,record,runtime,task_root):
        packet=super()._packet(record,runtime,task_root)
        self.order.freeze(task_id=record.task_id,checkpoint_id=record.checkpoint_id,
            features_path=task_root/"decision_features.json")
        return packet

    def _audit_complete(self,task_id):
        result=super()._audit_complete(task_id)
        providers=[r for r in read_jsonl(self.audit.response_path)if r["task_id"]==task_id]
        result["decision_order_valid"]=self.order.verify(task_id,providers)
        result["complete"]=bool(result["complete"]and result["decision_order_valid"])
        return result

    def _complete_journal(self,task_id,task_root,record):
        rows=[r for r in read_records(self.order.log.path)if r.get("task_id")==task_id]
        write_once_json(task_root/"decision_order_archive.json",rows)
        return super()._complete_journal(task_id,task_root,record)

    def run_task(self,task_id):
        load_causal_contract(self.root)
        return super().run_task(task_id)
