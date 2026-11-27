"""Explicit offline fixture; never imported by the live collector."""
from __future__ import annotations
import json
from .continue_acceptance_fixture import OfflineAcceptanceFactory


class FormalOfflineFactory(OfflineAcceptanceFactory):
    def __init__(self, tasks, transform=None):
        self.external_transform = transform
        super().__init__(tasks, transform=self.respond)

    def respond(self, task, runtime, response):
        call = response["choices"][0]["message"]["tool_calls"][0]["function"]
        history = runtime.tool_history
        if history and history[-1].error_type:
            last = history[-1]
            if "InvalidArgument" in last.error_type:
                call.update(name="final_answer", arguments=json.dumps({"answer": "Unresolved public validation error"}))
            elif last.tool_name == "update_order":
                call.update(name="get_order", arguments=json.dumps({"order_id": task.orders[0]["order_id"]}))
        elif len(history) >= 2 and history[-1].tool_name == "get_order" and history[-2].tool_name == "update_order" and history[-2].error_type:
            call.update(name="final_answer", arguments=json.dumps({"answer": history[-1].tool_result}))
        if call["name"] == "final_answer" and history and not history[-1].error_type:
            call["arguments"] = json.dumps({"answer": history[-1].tool_result})
        if self.external_transform:
            response = self.external_transform(task, runtime, response)
        return response
