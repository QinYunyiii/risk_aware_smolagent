"""Append-only, fsync-backed JSONL storage for local experiment records."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Mapping


class JsonlTraceWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: Mapping[str, object]) -> None:
        serialized = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        payload = memoryview((serialized + "\n").encode("utf-8"))
        with self._lock:
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                while payload:
                    written = os.write(descriptor, payload)
                    if written <= 0:
                        raise OSError("Failed to append trajectory record")
                    payload = payload[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

