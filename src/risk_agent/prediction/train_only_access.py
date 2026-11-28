"""Default-deny file access for trusted, local Train audit workers.

This is a Python audit discipline, not an OS sandbox for hostile native code.
It must be installed before third-party/project data loaders are imported.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_path(value: str | bytes | os.PathLike) -> Path:
    path = Path(os.fsdecode(value))
    if ".." in path.parts:
        raise ValueError("Parent traversal forbidden")
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Symlink component forbidden")
    return path


@dataclass
class ReadPolicy:
    """Exact data/support/output paths; enrollment closes before data analysis."""
    output: Path
    support: dict[str, str | None]
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: set[str] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)
    reads: dict[str, int] = field(default_factory=dict)
    sealed: bool = False
    installed: bool = False

    def enroll(self, path: Path, expected_sha256: str) -> None:
        if self.sealed:
            raise RuntimeError("Read policy is sealed")
        p = strict_path(path)
        if str(p) in self.support or not len(expected_sha256) == 64:
            raise ValueError("Invalid data registration")
        old = self.inputs.get(str(p))
        if old is not None and old != expected_sha256:
            raise ValueError("Conflicting input hash")
        self.inputs[str(p)] = expected_sha256

    def verify(self, path: Path) -> bytes:
        try:
            p = strict_path(path)
        except (ValueError, OSError):
            self.reject("input_validation", "unsafe input path")
        if str(p) not in self.inputs:
            self.reject("input_validation", "unregistered input")
        expected = self.inputs[str(p)]
        data = p.read_bytes()
        if sha256(data) != expected:
            self.events.append({"event": "input_hash_mismatch", "blocked": True})
            raise ValueError("Authorized input SHA-256 mismatch")
        return data

    def json(self, path: Path) -> Any:
        return json.loads(self.verify(path))

    def seal(self) -> None:
        self.inputs = MappingProxyType(dict(self.inputs))
        self.sealed = True

    def reject(self, event: str, reason: str, path: str | None = None) -> None:
        record = {"event": event, "reason": reason, "blocked": True}
        if path is not None:
            record["path"] = path
        self.events.append(record)
        raise PermissionError("Train-only access denied: " + reason)

    def hook(self, event: str, args: tuple) -> None:
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind",
                     "subprocess.Popen", "os.system", "os.posix_spawn", "os.fork", "os.forkpty"}:
            self.reject(event, "network or child process forbidden")
        if event in {"os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink", "os.truncate"}:
            self.reject(event, "file mutation operation forbidden")
        if event == "os.mkdir":
            # Output root is created empty before installation. No later directory
            # creation is needed by an audit worker running with -B.
            self.reject(event, "new directories forbidden after bootstrap")
        if event != "open":
            return
        if not isinstance(args[0], (str, bytes, os.PathLike)):
            self.reject(event, "unregistered file descriptor")
        try:
            path = str(strict_path(args[0]))
        except (ValueError, TypeError, OSError):
            self.reject(event, "unsafe path")
        mode = args[1] or ""
        flags = args[2] or 0
        writing = any(c in mode for c in "wax+") or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        if writing:
            if path not in self.outputs or not flags & os.O_EXCL or not flags & os.O_CREAT:
                self.reject(event, "only exclusive creation of declared outputs allowed")
            if Path(path).exists():
                self.reject(event, "output already exists")
            return
        if path not in self.inputs and path not in self.support:
            self.reject(event, "unregistered read", path)
        self.reads[path] = self.reads.get(path, 0) + 1

    def install(self) -> None:
        if self.installed:
            raise RuntimeError("Guard already installed")
        self.output = strict_path(self.output)
        self.outputs = {str(strict_path(p)) for p in self.outputs}
        if any(not Path(p).is_relative_to(self.output) for p in self.outputs):
            raise ValueError("Output path escape")
        self.outputs = frozenset(self.outputs)
        self.support = MappingProxyType(dict(self.support))
        sys.addaudithook(self.hook)
        self.installed = True

    def assert_clean(self) -> None:
        if self.events:
            raise RuntimeError("Blocked operation attempted; cannot publish PASS")

    def write_json(self, name: str, value: Any) -> None:
        path = self.output / name
        if str(path) not in self.outputs:
            raise ValueError("Undeclared audit output")
        with path.open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False, indent=2)
            f.write("\n")


def support_inventory(root: Path, extra_source_files: list[Path], cache_root: Path) -> dict[str, str | None]:
    """Bootstrap trusted runtime files, never datasets or arbitrary configs.

    Project files come from the Git index (not a suffix-based directory grant).
    Day 1 needs no third-party packages. No virtualenv directory is granted.
    """
    import subprocess
    import sysconfig
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    paths = {root / n for n in tracked if n and n.endswith(".py") and n.split("/")[0] in {"src", "scripts", "tests"}}
    paths.update(extra_source_files)
    stdlib = Path(sysconfig.get_path("stdlib"))
    for directory in (stdlib,):
        for base, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink() and d not in {"__pycache__", "test", "tests"}]
            for name in files:
                p = Path(base) / name
                if name.endswith((".py", ".so")):
                    if not p.is_symlink():
                        paths.add(p)
    result: dict[str, str | None] = {}
    sys.pycache_prefix = str(cache_root)
    for path in paths:
        p = strict_path(path)
        result[str(p)] = sha256(p.read_bytes())
        if p.suffix == ".py":
            # Isolated, new cache prefix; -B prevents writes. Only these exact
            # absent cache paths may be probed by Python's import machinery.
            result[importlib.util.cache_from_source(str(p))] = None
    # CPython probes optional zip entries on sys.path, even if absent.
    for value in sys.path:
        p = Path(value)
        if p.name == "python312.zip" and p.parent == stdlib.parent:
            result[str(p)] = sha256(p.read_bytes()) if p.is_file() else None
    return result
