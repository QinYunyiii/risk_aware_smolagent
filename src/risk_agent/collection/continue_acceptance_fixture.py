"""Explicit offline-only SDK responses for acceptance wiring, never model data.

Reference steps and expected answers are consumed ONLY by this mock provider.
The acceptance runner never uses them to choose a real model action or result.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx2
from smolagents import OpenAIServerModel

from risk_agent.agent import CheckpointableToolCallingAgent, RecordingModel
from risk_agent.benchmark.step_references import resolve_step_value


class OfflineAcceptanceFactory:
    def __init__(self, tasks: Any, transform: Callable[..., Any] | None = None) -> None:
        self.tasks = {task.seed: task for task in tasks}
        self.requests: list[dict[str, Any]] = []
        self.transform = transform

    def __call__(self, settings: Any, worker: Any, tools: Any, runtime: Any, *,
                 max_steps: int, max_model_calls: int, agent_name: str):
        task = self.tasks[runtime.seed]

        def handler(request: httpx2.Request) -> httpx2.Response:
            body = json.loads(request.content)
            self.requests.append({"task_id": task.task_id, "worker_id": worker.worker_id, "body": body})
            completed = [entry for entry in runtime.tool_history if entry.error_type is None]
            index = len(completed)
            if index < len(task.reference_steps):
                step = task.reference_steps[index]
                name = step.tool
                arguments = resolve_step_value(step.arguments, {
                    f"step_{i + 1}": entry.tool_result for i, entry in enumerate(completed)})
            else:
                name, arguments = "final_answer", {"answer": task.expected["result"]}
            response = {
                "id": f"offline-{task.task_id}-{len(self.requests)}", "object": "chat.completion",
                "created": 1800000000, "model": settings.model_id,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "",
                    "tool_calls": [{"id": f"{task.task_id}-call-{len(runtime.tool_history) + 1}",
                        "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]},
                    "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
            }
            if self.transform is not None:
                response = self.transform(task, runtime, response)
            return httpx2.Response(200, json=response)

        client = httpx2.Client(transport=httpx2.MockTransport(handler), trust_env=False)
        inference = settings.inference
        underlying = OpenAIServerModel(
            model_id=settings.model_id, api_base=worker.base_url, api_key="offline-fixture-value",
            client_kwargs={"http_client": client, "max_retries": 0}, retry=False,
            seed=inference["seed"], temperature=inference["temperature"], max_tokens=inference["max_tokens"],
            extra_body={"chat_template_kwargs": inference["chat_template_kwargs"]})
        underlying.model_version = settings.model_version
        underlying.worker_id = worker.worker_id
        underlying.endpoint = worker.base_url
        try:
            recorder = RecordingModel(underlying, runtime_state=runtime, max_model_calls=max_model_calls)
            agent = CheckpointableToolCallingAgent(tools=tools, model=recorder, max_steps=max_steps,
                verbosity_level=0, name=agent_name, description="Explicit offline acceptance fixture")
        except BaseException:
            underlying.client.close()
            raise
        return agent, recorder, underlying
