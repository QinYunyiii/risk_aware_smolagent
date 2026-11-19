"""TLS-verified OpenAI-compatible clients and smolagents factories."""

from __future__ import annotations

import ssl
import socket
import time
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx2
import openai
from smolagents import OpenAIServerModel, Tool, ToolCallingAgent

from risk_agent.tools.calculation_tools import CalculatorTool

from .worker_router import RemoteModelSettings, WorkerConfig


class ModelErrorCategory(StrEnum):
    TIMEOUT = "model_api_timeout"
    TLS = "model_api_tls_error"
    UNAVAILABLE = "model_api_unavailable"
    AUTHENTICATION = "model_api_authentication_error"
    RATE_LIMIT = "model_api_rate_limit"
    RESPONSE = "model_api_response_error"
    UNKNOWN = "model_api_unknown_error"


class ModelServiceError(RuntimeError):
    def __init__(self, category: ModelErrorCategory, worker_id: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.worker_id = worker_id


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def classify_model_exception(exc: BaseException) -> ModelErrorCategory:
    chain = _exception_chain(exc)
    if any(isinstance(item, (openai.APITimeoutError, TimeoutError)) for item in chain):
        return ModelErrorCategory.TIMEOUT
    if any(isinstance(item, ssl.SSLCertVerificationError) for item in chain):
        return ModelErrorCategory.TLS
    lowered = " ".join(str(item).lower() for item in chain)
    if "certificate verify failed" in lowered or "ssl" in lowered and "certificate" in lowered:
        return ModelErrorCategory.TLS
    if any(isinstance(item, openai.AuthenticationError) for item in chain):
        return ModelErrorCategory.AUTHENTICATION
    if any(isinstance(item, openai.RateLimitError) for item in chain):
        return ModelErrorCategory.RATE_LIMIT
    if any(isinstance(item, (openai.APIConnectionError, ConnectionError)) for item in chain):
        return ModelErrorCategory.UNAVAILABLE
    if any(isinstance(item, openai.APIStatusError) for item in chain):
        return ModelErrorCategory.RESPONSE
    return ModelErrorCategory.UNKNOWN


def _ssl_context(ca_bundle: Path) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(ca_bundle))


def _http_client(settings: RemoteModelSettings, ca_bundle: Path) -> httpx2.Client:
    timeout = httpx2.Timeout(
        timeout=settings.request_timeout_seconds,
        connect=settings.connect_timeout_seconds,
    )
    return httpx2.Client(verify=_ssl_context(ca_bundle), timeout=timeout, trust_env=False)


@dataclass(frozen=True, slots=True)
class ChatResponse:
    worker_id: str
    endpoint: str
    model_id: str
    model_version: str
    content: str
    input_tokens: int
    output_tokens: int
    latency_seconds: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RemoteAPIClient:
    """Small direct client used for health checks and ordinary chat smoke tests."""

    def __init__(self, settings: RemoteModelSettings, worker: WorkerConfig) -> None:
        self.settings = settings
        self.worker = worker
        self._http_client = _http_client(settings, settings.resolve_ca_bundle())
        self._client = openai.OpenAI(
            api_key=settings.resolve_api_key(),
            base_url=worker.base_url,
            timeout=settings.request_timeout_seconds,
            max_retries=0,
            http_client=self._http_client,
        )

    def close(self) -> None:
        self._client.close()

    def list_model_ids(self) -> list[str]:
        try:
            return [item.id for item in self._client.models.list().data]
        except Exception as exc:
            raise ModelServiceError(
                classify_model_exception(exc), self.worker.worker_id, "Model list request failed"
            ) from exc

    def chat(self, prompt: str) -> ChatResponse:
        started = time.perf_counter()
        inference = self.settings.inference
        try:
            response = self._client.chat.completions.create(
                model=self.settings.model_id,
                messages=[{"role": "user", "content": prompt}],
                seed=int(inference.get("seed", 20261118)),
                temperature=float(inference.get("temperature", 0.0)),
                max_tokens=int(inference.get("max_tokens", 256)),
                extra_body={"chat_template_kwargs": inference.get("chat_template_kwargs", {})},
            )
        except Exception as exc:
            raise ModelServiceError(
                classify_model_exception(exc), self.worker.worker_id, "Chat completion failed"
            ) from exc
        usage = response.usage
        return ChatResponse(
            worker_id=self.worker.worker_id,
            endpoint=self.worker.base_url,
            model_id=self.settings.model_id,
            model_version=self.settings.model_version,
            content=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_seconds=time.perf_counter() - started,
        )


def verify_tls_endpoint(
    settings: RemoteModelSettings,
    worker: WorkerConfig,
) -> float:
    """Perform an explicit certificate/hostname handshake with the configured CA."""
    parsed = urlparse(worker.base_url)
    host = parsed.hostname
    if host is None:
        raise ModelServiceError(ModelErrorCategory.TLS, worker.worker_id, "TLS endpoint has no host")
    port = parsed.port or 443
    started = time.perf_counter()
    try:
        with socket.create_connection(
            (host, port), timeout=settings.connect_timeout_seconds
        ) as raw_socket:
            with _ssl_context(settings.resolve_ca_bundle()).wrap_socket(
                raw_socket, server_hostname=host
            ):
                pass
    except Exception as exc:
        raise ModelServiceError(
            classify_model_exception(exc), worker.worker_id, "TLS preflight failed"
        ) from exc
    return time.perf_counter() - started


def create_tool_calling_agent(
    settings: RemoteModelSettings,
    worker: WorkerConfig,
    calculator: CalculatorTool,
) -> tuple[ToolCallingAgent, OpenAIServerModel]:
    http_client = _http_client(settings, settings.resolve_ca_bundle())
    inference = settings.inference
    model = OpenAIServerModel(
        model_id=settings.model_id,
        api_base=worker.base_url,
        api_key=settings.resolve_api_key(),
        client_kwargs={"http_client": http_client, "max_retries": 0},
        retry=False,
        seed=int(inference.get("seed", 20261118)),
        temperature=float(inference.get("temperature", 0.0)),
        max_tokens=int(inference.get("max_tokens", 256)),
        extra_body={"chat_template_kwargs": inference.get("chat_template_kwargs", {})},
    )
    agent = ToolCallingAgent(
        tools=[calculator],
        model=model,
        max_steps=3,
        verbosity_level=0,
        name=f"day2_{worker.worker_id.replace('-', '_')}",
        description="Day 2 local calculator smoke agent",
    )
    return agent, model


def create_benchmark_agent(
    settings: RemoteModelSettings,
    worker: WorkerConfig,
    tools: list[Tool],
    *,
    max_steps: int,
    agent_name: str,
) -> tuple[ToolCallingAgent, OpenAIServerModel]:
    """Create a Day 8 task agent while retaining TLS and no-retry guarantees."""
    if not tools:
        raise ValueError("Benchmark agent requires at least one local tool")
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    http_client = _http_client(settings, settings.resolve_ca_bundle())
    inference = settings.inference
    model = OpenAIServerModel(
        model_id=settings.model_id,
        api_base=worker.base_url,
        api_key=settings.resolve_api_key(),
        client_kwargs={"http_client": http_client, "max_retries": 0},
        retry=False,
        seed=int(inference.get("seed", 20261118)),
        temperature=float(inference.get("temperature", 0.0)),
        max_tokens=int(inference.get("max_tokens", 256)),
        extra_body={"chat_template_kwargs": inference.get("chat_template_kwargs", {})},
    )
    agent = ToolCallingAgent(
        tools=tools,
        model=model,
        max_steps=max_steps,
        verbosity_level=0,
        name=agent_name,
        description="Day 8 MiniToolBench-R agent with local WSL tool execution",
    )
    return agent, model


def run_calculator_agent(
    settings: RemoteModelSettings,
    worker: WorkerConfig,
    expression: str,
    expected_result: str,
) -> dict[str, Any]:
    calculator = CalculatorTool()
    agent, model = create_tool_calling_agent(settings, worker, calculator)
    run_id = str(uuid.uuid4())
    task = (
        "You must call the calculator tool exactly once with this expression: "
        f"{expression}. After the tool returns, answer with only the calculator result."
    )
    started = time.perf_counter()

    def observed_token_usage() -> dict[str, int] | None:
        input_tokens = 0
        output_tokens = 0
        observed = False
        for step in agent.memory.steps:
            usage = getattr(step, "token_usage", None)
            if usage is not None:
                observed = True
                input_tokens += int(usage.input_tokens)
                output_tokens += int(usage.output_tokens)
        if not observed:
            return None
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

    try:
        result = agent.run(task, return_full_result=True)
        tool_events = [event.to_dict() for event in calculator.executions]
        tool_success = any(event.get("result") == expected_result for event in tool_events)
        token_usage = result.token_usage.dict() if result.token_usage is not None else None
        return {
            "run_id": run_id,
            "worker_id": worker.worker_id,
            "endpoint": worker.base_url,
            "model_id": settings.model_id,
            "model_version": settings.model_version,
            "prompt_version": settings.prompt_version,
            "inference": settings.inference,
            "task_kind": "day2_calculator_smoke",
            "expression": expression,
            "expected_result": expected_result,
            "agent_state": result.state,
            "agent_output": str(result.output),
            "token_usage": token_usage,
            "token_usage_complete": True,
            "latency_seconds": time.perf_counter() - started,
            "tool_executions": tool_events,
            "tool_success": tool_success,
            "error_type": None,
        }
    except Exception as exc:
        category = classify_model_exception(exc)
        tool_events = [event.to_dict() for event in calculator.executions]
        tool_success = any(event.get("result") == expected_result for event in tool_events)
        return {
            "run_id": run_id,
            "worker_id": worker.worker_id,
            "endpoint": worker.base_url,
            "model_id": settings.model_id,
            "model_version": settings.model_version,
            "prompt_version": settings.prompt_version,
            "inference": settings.inference,
            "task_kind": "day2_calculator_smoke",
            "expression": expression,
            "expected_result": expected_result,
            "agent_state": "error",
            "agent_output": None,
            "token_usage": observed_token_usage(),
            "token_usage_complete": False,
            "latency_seconds": time.perf_counter() - started,
            "tool_executions": tool_events,
            "tool_success": tool_success,
            "error_type": category.value,
            "error_message": type(exc).__name__,
        }
    finally:
        model.client.close()

