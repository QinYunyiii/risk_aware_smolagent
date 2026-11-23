from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from smolagents import OpenAIServerModel
from smolagents.models import ChatMessage, MessageRole, is_rate_limit_error
from smolagents.utils import Retrying

from risk_agent.agent import ModelRequestRejected, RecordingModel
from risk_agent.agent import agent_factory
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.tools.calculation_tools import CalculatorTool


def _real_openai_model(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> OpenAIServerModel:
    http_client = httpx2.Client(
        transport=httpx2.MockTransport(handler),
        trust_env=False,
    )
    return OpenAIServerModel(
        model_id="qwen3-8b-retry-fixture",
        api_base="https://worker.test/v1",
        api_key="fixture-key-not-a-secret",
        client_kwargs={"http_client": http_client, "max_retries": 0},
        retry=False,
        temperature=0.0,
        max_tokens=8,
    )


def _unused_response(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError(f"Provider must not be called: {request.url}")


def _settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[RemoteModelSettings, WorkerConfig]:
    ca_bundle = tmp_path / "unused-ca.pem"
    ca_bundle.write_text(
        "not used because the HTTP client factory is patched", encoding="utf-8"
    )
    monkeypatch.setenv("B2_RETRY_TEST_API_KEY", "fixture-key-not-a-secret")
    monkeypatch.setenv("B2_RETRY_TEST_CA", str(ca_bundle))
    worker = WorkerConfig(worker_id="worker-test-0", base_url="https://worker.test/v1")
    settings = RemoteModelSettings(
        model_id="qwen3-8b-retry-fixture",
        model_version="fixture-version",
        prompt_version="fixture-prompt",
        api_key_env="B2_RETRY_TEST_API_KEY",
        ca_bundle_env="B2_RETRY_TEST_CA",
        request_timeout_seconds=30.0,
        connect_timeout_seconds=5.0,
        router_strategy="round_robin",
        inference={"seed": 7, "temperature": 0.0, "max_tokens": 8},
        workers=(
            worker,
            WorkerConfig(worker_id="worker-test-1", base_url="https://worker-1.test/v1"),
        ),
    )
    return settings, worker


def test_agent_factories_create_verifiable_single_attempt_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, worker = _settings(tmp_path, monkeypatch)
    clients: list[httpx2.Client] = []

    def local_http_client(*args: Any, **kwargs: Any) -> httpx2.Client:
        client = httpx2.Client(
            transport=httpx2.MockTransport(_unused_response), trust_env=False
        )
        clients.append(client)
        return client

    monkeypatch.setattr(agent_factory, "_http_client", local_http_client)
    _, tool_model = agent_factory.create_tool_calling_agent(
        settings, worker, CalculatorTool()
    )
    _, benchmark_model = agent_factory.create_benchmark_agent(
        settings,
        worker,
        [CalculatorTool()],
        max_steps=3,
        agent_name="retry_fixture_agent",
    )
    try:
        for underlying in (tool_model, benchmark_model):
            assert type(underlying.retryer) is Retrying
            assert underlying.retryer.max_attempts == 1
            assert underlying.retryer.retry_predicate is is_rate_limit_error
            assert underlying.client.max_retries == 0
            assert underlying.client_kwargs["max_retries"] == 0
            fingerprint = RecordingModel(underlying).model_fingerprint
            assert fingerprint["retryer"]["stop_policy"] == {
                "type": "max_attempts",
                "max_attempts": 1,
            }
            assert fingerprint["retryer"]["retry_policy"] == {
                "module": "smolagents.models",
                "qualname": "is_rate_limit_error",
            }
            assert fingerprint["client"]["max_retries"] == 0
            assert "object_identity" not in str(fingerprint)
    finally:
        tool_model.client.close()
        benchmark_model.client.close()
    assert len(clients) == 2


@pytest.mark.parametrize(
    ("mutation", "difference"),
    [
        ("max_attempts", "underlying_no_retry_configuration"),
        ("retryer_replacement", "underlying_retryer_replaced"),
        ("client_max_retries", "underlying_no_retry_configuration"),
        ("client_kwargs_max_retries", "underlying_no_retry_configuration"),
        ("retry_policy", "underlying_no_retry_configuration"),
        ("wait_policy", "underlying_retryer_configuration"),
    ],
)
def test_runtime_retry_configuration_mutation_rejects_before_provider(
    mutation: str, difference: str
) -> None:
    underlying = _real_openai_model(_unused_response)
    model = RecordingModel(underlying, max_model_calls=2)
    try:
        if mutation == "max_attempts":
            underlying.retryer.max_attempts = 2
        elif mutation == "retryer_replacement":
            underlying.retryer = Retrying(
                max_attempts=1,
                wait_seconds=60,
                exponential_base=2,
                jitter=True,
                retry_predicate=is_rate_limit_error,
                reraise=True,
            )
        elif mutation == "client_max_retries":
            underlying.client.max_retries = 2
        elif mutation == "client_kwargs_max_retries":
            underlying.client_kwargs["max_retries"] = 2
        elif mutation == "retry_policy":
            underlying.retryer.retry_predicate = lambda exc: True
        else:
            underlying.retryer.wait_seconds = 0

        with pytest.raises(ModelRequestRejected, match=difference):
            model.generate([ChatMessage(role=MessageRole.USER, content="must not run")])

        assert model.executed_calls == 0
        assert model.succeeded_calls == 0
        assert model.requests[-1].difference == difference
    finally:
        underlying.client.close()


@pytest.mark.parametrize("unverifiable", ["retryer", "client"])
def test_unverifiable_production_retry_configuration_fails_at_binding(
    unverifiable: str,
) -> None:
    underlying = _real_openai_model(_unused_response)
    if unverifiable == "retryer":
        underlying.retryer = object()
    else:
        del underlying.client.max_retries
    try:
        with pytest.raises(ModelRequestRejected, match="underlying_(retryer|client)"):
            RecordingModel(underlying)
    finally:
        underlying.client.close()


def test_real_openai_http_429_is_attempted_exactly_once() -> None:
    requests: list[httpx2.Request] = []

    def rate_limited(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            status_code=429,
            headers={"x-request-id": "request-429-fixture"},
            json={
                "error": {
                    "message": "fixture rate limit",
                    "type": "rate_limit_error",
                    "code": "rate_limit_exceeded",
                }
            },
        )

    underlying = _real_openai_model(rate_limited)
    model = RecordingModel(underlying, max_model_calls=1)
    messages = [ChatMessage(role=MessageRole.USER, content="trigger one HTTP request")]
    try:
        with pytest.raises(openai.RateLimitError, match="fixture rate limit"):
            model.generate(messages)
        with pytest.raises(ModelRequestRejected, match="model_call_budget"):
            model.generate(messages)

        assert len(requests) == 1
        assert requests[0].url == httpx2.URL(
            "https://worker.test/v1/chat/completions"
        )
        assert underlying.retryer.max_attempts == 1
        assert underlying.client.max_retries == 0
        assert model.attempted_calls == 2
        assert model.executed_calls == 1
        assert model.succeeded_calls == 0
        assert model.model_calls_used == 1
        assert model.requests[0].status == "failed"
        assert model.requests[0].prompt_tokens is None
        assert model.requests[0].completion_tokens is None
        assert model.requests[1].difference == "model_call_budget"
    finally:
        underlying.client.close()
