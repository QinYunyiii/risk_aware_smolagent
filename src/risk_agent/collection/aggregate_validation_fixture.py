"""Explicit Mock SDK path through read_csv then aggregate_records, never live."""
from __future__ import annotations

import json

from .aggregate_target_policy import applicable
from .formal_fixture import FormalOfflineFactory


class AggregateValidationOfflineFactory(FormalOfflineFactory):
    def __init__(self, tasks, plan, *, source_aggregate=False, transform=None):
        self.targets = {c["task_id"] for c in plan["tasks"] if applicable(c)}
        self.source_aggregate = source_aggregate
        super().__init__(tasks, transform=transform)

    def respond(self, task, runtime, response):
        # Superclass transform runs last below, so tests can still inject audit failures.
        transform = self.external_transform
        self.external_transform = None
        try:
            response = super().respond(task, runtime, response)
        finally:
            self.external_transform = transform
        if task.task_id in self.targets:
            parameters = dict(task.reference_steps[0].arguments)
            function = response["choices"][0]["message"]["tool_calls"][0]["function"]
            history = runtime.tool_history
            reads = [entry for entry in history if entry.tool_name == "read_csv" and not entry.error_type]
            aggregates = [entry for entry in history
                          if entry.tool_name in ("aggregate_data", "aggregate_records") and not entry.error_type]
            if not history and self.source_aggregate:
                name, arguments = "aggregate_data", parameters
            elif not reads:
                name, arguments = "read_csv", {"path": parameters["path"]}
            elif not aggregates:
                name = "aggregate_records"
                arguments = {key: value for key, value in parameters.items() if key != "path"}
                arguments["records"] = reads[-1].tool_result
            else:
                name, arguments = "final_answer", {"answer": aggregates[-1].tool_result}
            function.update(name=name, arguments=json.dumps(arguments))
        return transform(task, runtime, response) if transform else response
