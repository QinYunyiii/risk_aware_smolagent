#!/usr/bin/env python3
"""Run Day 2 direct-chat and model-service error checks against both workers."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from risk_agent.agent.agent_factory import (
    ChatResponse,
    ModelErrorCategory,
    ModelServiceError,
    RemoteAPIClient,
    verify_tls_endpoint,
)
from risk_agent.agent.worker_router import RemoteModelSettings, WorkerConfig
from risk_agent.monitoring.trajectory import JsonlTraceWriter


PROMPTS = (
    "Reply with only the word READY.",
    "What is 2 plus 3? Reply with only the number.",
    "Reply with only the lowercase word secure.",
    "Which is larger, 9 or 4? Reply with only the number.",
    "Reply with only the word DONE.",
)


@contextmanager
def hanging_tcp_server() -> Iterator[int]:
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    server.settimeout(0.2)
    stopped = threading.Event()

    def serve() -> None:
        connections: list[socket.socket] = []
        try:
            while not stopped.is_set():
                try:
                    connection, _ = server.accept()
                    connections.append(connection)
                except TimeoutError:
                    continue
                except OSError:
                    if stopped.is_set():
                        break
                    raise
        finally:
            for connection in connections:
                connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield int(server.getsockname()[1])
    finally:
        stopped.set()
        server.close()
        thread.join(timeout=1)


def expected_failure(
    name: str,
    settings: RemoteModelSettings,
    worker: WorkerConfig,
    expected_category: str,
) -> dict[str, object]:
    started = time.perf_counter()
    client = RemoteAPIClient(settings, worker)
    try:
        client.list_model_ids()
    except ModelServiceError as exc:
        return {
            "check": name,
            "passed": exc.category.value == expected_category,
            "expected_category": expected_category,
            "actual_category": exc.category.value,
            "worker_id": worker.worker_id,
            "endpoint": worker.base_url,
            "latency_seconds": time.perf_counter() - started,
        }
    finally:
        client.close()
    return {
        "check": name,
        "passed": False,
        "expected_category": expected_category,
        "actual_category": "request_succeeded_unexpectedly",
        "worker_id": worker.worker_id,
        "endpoint": worker.base_url,
        "latency_seconds": time.perf_counter() - started,
    }


def chat_with_bounded_retry(
    settings: RemoteModelSettings,
    worker: WorkerConfig,
    prompt: str,
) -> tuple[ChatResponse, int]:
    """Retry one read-only chat once on a transient transport failure."""
    transient = {ModelErrorCategory.TIMEOUT, ModelErrorCategory.UNAVAILABLE}
    for attempt in (1, 2):
        client = RemoteAPIClient(settings, worker)
        try:
            return client.chat(prompt), attempt
        except ModelServiceError as exc:
            if attempt == 2 or exc.category not in transient:
                raise
            time.sleep(0.5)
        finally:
            client.close()
    raise AssertionError("unreachable")


def expected_tls_failure(
    settings: RemoteModelSettings,
    worker: WorkerConfig,
) -> dict[str, object]:
    started = time.perf_counter()
    try:
        verify_tls_endpoint(settings, worker)
    except ModelServiceError as exc:
        return {
            "check": "tls_verification",
            "passed": exc.category == ModelErrorCategory.TLS,
            "expected_category": ModelErrorCategory.TLS.value,
            "actual_category": exc.category.value,
            "worker_id": worker.worker_id,
            "endpoint": worker.base_url,
            "latency_seconds": time.perf_counter() - started,
        }
    return {
        "check": "tls_verification",
        "passed": False,
        "expected_category": ModelErrorCategory.TLS.value,
        "actual_category": "request_succeeded_unexpectedly",
        "worker_id": worker.worker_id,
        "endpoint": worker.base_url,
        "latency_seconds": time.perf_counter() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/remote_model.yaml")
    parser.add_argument("--output", default="runs/day02/remote_api_checks.jsonl")
    parser.add_argument(
        "--skip-chats",
        action="store_true",
        help="Reuse append-only successful chat records and only rerun health/failure checks.",
    )
    args = parser.parse_args()
    settings = RemoteModelSettings.from_yaml(args.config)
    writer = JsonlTraceWriter(args.output)
    summary: dict[str, object] = {"model_lists": {}, "chat_counts": {}, "failure_checks": []}
    passed = True

    for worker in settings.workers:
        tls_latency = verify_tls_endpoint(settings, worker)
        writer.append(
            {
                "record_type": "tls_preflight",
                "worker_id": worker.worker_id,
                "endpoint": worker.base_url,
                "verified": True,
                "latency_seconds": tls_latency,
            }
        )
        client = RemoteAPIClient(settings, worker)
        try:
            model_ids = client.list_model_ids()
            summary["model_lists"][worker.worker_id] = model_ids  # type: ignore[index]
            if settings.model_id not in model_ids:
                passed = False
        finally:
            client.close()
        if not args.skip_chats:
            for index, prompt in enumerate(PROMPTS, start=1):
                response, attempt_count = chat_with_bounded_retry(settings, worker, prompt)
                record = {
                    "record_type": "direct_chat",
                    "chat_index": index,
                    "prompt": prompt,
                    "attempt_count": attempt_count,
                    **response.to_dict(),
                }
                writer.append(record)

    live = settings.workers[0]
    prior_test_ca = os.environ.get("RISK_AGENT_TEST_CA")
    os.environ["RISK_AGENT_TEST_CA"] = "/etc/ssl/certs/ca-certificates.crt"
    try:
        tls_settings = replace(settings, ca_bundle_env="RISK_AGENT_TEST_CA")
        failures = [
            expected_tls_failure(tls_settings, live),
            expected_failure(
                "service_unavailable",
                settings,
                WorkerConfig("unavailable-test", "https://localhost:65534/v1"),
                "model_api_unavailable",
            ),
        ]
        with hanging_tcp_server() as port:
            timeout_settings = replace(
                settings, request_timeout_seconds=0.25, connect_timeout_seconds=0.25
            )
            failures.append(
                expected_failure(
                    "request_timeout",
                    timeout_settings,
                    WorkerConfig("timeout-test", f"https://localhost:{port}/v1"),
                    "model_api_timeout",
                )
            )
    finally:
        if prior_test_ca is None:
            os.environ.pop("RISK_AGENT_TEST_CA", None)
        else:
            os.environ["RISK_AGENT_TEST_CA"] = prior_test_ca

    for failure in failures:
        writer.append({"record_type": "model_failure_check", **failure})
        passed = passed and bool(failure["passed"])
    accumulated = [
        json.loads(line)
        for line in Path(args.output).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    chat_counts = Counter(
        record.get("worker_id")
        for record in accumulated
        if record.get("record_type") == "direct_chat"
    )
    summary["chat_counts"] = {
        worker.worker_id: chat_counts[worker.worker_id] for worker in settings.workers
    }
    passed = passed and all(chat_counts[worker.worker_id] >= 5 for worker in settings.workers)
    summary["failure_checks"] = failures
    summary["passed"] = passed
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
