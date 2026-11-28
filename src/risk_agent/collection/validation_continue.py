"""Validation-only identity gate over the exact frozen CONTINUE method."""
from __future__ import annotations

from .formal_continue import FormalContinueRunner
from .continue_acceptance_v2 import OutputContractContinueGateRunner
from .continue_gate import ContinueGateError


class ValidationContinueRunner(FormalContinueRunner):
    def _monitor(self,**kwargs):
        monitor=super()._monitor(**kwargs)
        monitor.context["split"]="validation"
        return monitor

    def _validate_identity(self, verified, worker) -> None:
        OutputContractContinueGateRunner._validate_identity(self,verified,worker)
        identity=verified.manifest.identity
        task=self.tasks.get(identity["task_id"])
        if (identity["execution_mode"]!=self.research_flags["execution_mode"]
                or identity["experiment_id"]!=self.experiment_id
                or identity["run_id"]!=self.run_id or identity["split"]!="validation"
                or task is None or task.split.value!="validation"):
            raise ContinueGateError("Validation cannot import Train/Test, other-run or other-mode Checkpoints")
