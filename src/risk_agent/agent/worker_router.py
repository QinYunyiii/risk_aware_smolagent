"""Configuration and deterministic routing for independent model workers."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml


class ConfigurationError(ValueError):
    """Raised when remote model configuration is unsafe or incomplete."""


class UnsafeWorkerSwitchError(RuntimeError):
    """Raised when failover could repeat a tool operation with side effects."""


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    worker_id: str
    base_url: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https":
            raise ConfigurationError(f"Worker {self.worker_id!r} must use HTTPS")
        if not parsed.hostname:
            raise ConfigurationError(f"Worker {self.worker_id!r} has an invalid base URL")


@dataclass(frozen=True, slots=True)
class RemoteModelSettings:
    model_id: str
    model_version: str
    prompt_version: str
    api_key_env: str
    ca_bundle_env: str
    request_timeout_seconds: float
    connect_timeout_seconds: float
    router_strategy: Literal["round_robin"]
    inference: dict[str, object]
    workers: tuple[WorkerConfig, ...]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RemoteModelSettings":
        config_path = Path(path)
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ConfigurationError("Remote model configuration must be a mapping")
        workers_raw = raw.get("workers")
        if not isinstance(workers_raw, list) or len(workers_raw) < 2:
            raise ConfigurationError("At least two worker definitions are required")
        workers = tuple(
            WorkerConfig(worker_id=str(item["worker_id"]), base_url=str(item["base_url"]))
            for item in workers_raw
        )
        ids = [worker.worker_id for worker in workers]
        if len(ids) != len(set(ids)):
            raise ConfigurationError("Worker IDs must be unique")
        strategy = raw.get("router_strategy", "round_robin")
        if strategy != "round_robin":
            raise ConfigurationError(f"Unsupported router strategy: {strategy!r}")
        return cls(
            model_id=str(raw["model_id"]),
            model_version=str(raw["model_version"]),
            prompt_version=str(raw["prompt_version"]),
            api_key_env=str(raw["api_key_env"]),
            ca_bundle_env=str(raw["ca_bundle_env"]),
            request_timeout_seconds=float(raw["request_timeout_seconds"]),
            connect_timeout_seconds=float(raw["connect_timeout_seconds"]),
            router_strategy="round_robin",
            inference=dict(raw.get("inference", {})),
            workers=workers,
        )

    def resolve_api_key(self) -> str:
        value = os.environ.get(self.api_key_env)
        if not value:
            raise ConfigurationError(f"Required API key environment variable {self.api_key_env!r} is unset")
        return value

    def resolve_ca_bundle(self) -> Path:
        value = os.environ.get(self.ca_bundle_env)
        if not value:
            raise ConfigurationError(f"Required CA bundle environment variable {self.ca_bundle_env!r} is unset")
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ConfigurationError(f"CA bundle does not exist: {path}")
        return path


class WorkerRouter:
    """Thread-safe explicit and round-robin worker selection."""

    def __init__(self, workers: tuple[WorkerConfig, ...]) -> None:
        if len(workers) < 2:
            raise ValueError("WorkerRouter requires at least two workers")
        self._workers = workers
        self._by_id = {worker.worker_id: worker for worker in workers}
        self._next_index = 0
        self._lock = threading.Lock()

    @property
    def workers(self) -> tuple[WorkerConfig, ...]:
        return self._workers

    def select(self, worker_id: str | None = None) -> WorkerConfig:
        if worker_id is not None:
            try:
                return self._by_id[worker_id]
            except KeyError as exc:
                raise KeyError(f"Unknown worker_id: {worker_id}") from exc
        with self._lock:
            worker = self._workers[self._next_index]
            self._next_index = (self._next_index + 1) % len(self._workers)
            return worker

    def failover(self, current_worker_id: str, *, tool_side_effects_committed: bool) -> WorkerConfig:
        if tool_side_effects_committed:
            raise UnsafeWorkerSwitchError(
                "Worker failover is forbidden after a tool may have committed side effects"
            )
        current = self.select(current_worker_id)
        for worker in self._workers:
            if worker.worker_id != current.worker_id:
                return worker
        raise RuntimeError("No alternate worker is available")

