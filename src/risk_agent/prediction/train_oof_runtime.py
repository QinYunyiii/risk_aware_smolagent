"""Day 4 explicitly inventoried numerical runtime; no directory grants."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

def numerical_support(root: Path, config: dict, access) -> dict[str, str | None]:
    strict_path, sha256 = access.strict_path, access.sha256
    site = root / ".venv/lib/python3.12/site-packages"
    paths = set()
    for package in config["runtime_code_packages"]:
        if not package.isidentifier():
            raise ValueError("Invalid numerical package name")
        directory = strict_path(site / package)
        for base, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", "test", "tests"} and not (Path(base) / d).is_symlink()]
            for name in files:
                if name.endswith((".py", ".so")):
                    paths.add(strict_path(Path(base) / name))
    for relative in [*config["runtime_code_modules"], *config["runtime_resources"]]:
        path = strict_path(site / relative)
        if not path.is_relative_to(site):
            raise ValueError("Runtime support escaped numerical environment")
        paths.add(path)
    result = {}
    for path in paths:
        result[str(path)] = sha256(path.read_bytes())
        if path.suffix == ".py": result[importlib.util.cache_from_source(str(path))] = None
    for relative in config["runtime_optional_resources"]:
        path = strict_path(site / relative)
        if not path.is_relative_to(site):raise ValueError("Optional runtime metadata escaped environment")
        result[str(path)] = sha256(path.read_bytes()) if path.is_file() else None
    # importlib.metadata scans installed distribution entry points during sklearn
    # initialization. Register exact metadata files, not package data directories.
    for directory in site.glob("*.dist-info"):
        for name in config["runtime_distribution_metadata"]:
            if name not in {"entry_points.txt", "METADATA"}:raise ValueError("Unsupported distribution metadata role")
            path = strict_path(directory / name)
            result[str(path)] = sha256(path.read_bytes()) if path.is_file() else None
    return result


def runtime_policy_class(access, absent_support_paths=()):
    absent = frozenset(absent_support_paths)
    class NumericalReadPolicy(access.ReadPolicy):
        """Only additional system read: own process library map for threadpoolctl."""
        def hook(self, event, args):
            if event == "open" and isinstance(args[0], (str, bytes)) and os.fsdecode(args[0]) in absent:
                if Path(os.fsdecode(args[0])).exists():
                    self.reject(event, "previously absent support file appeared during execution")
            if event == "open" and isinstance(args[0], (str, bytes)) and os.fsdecode(args[0]) == "/proc/self/maps":
                mode, flags = args[1] or "", args[2] or 0
                writing = any(c in mode for c in "wax+") or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
                if writing or Path("/proc/self/maps").resolve() != Path(f"/proc/{os.getpid()}/maps"):
                    self.reject(event, "invalid own-process runtime map access")
                self.reads["runtime_system:/proc/self/maps"] = self.reads.get("runtime_system:/proc/self/maps", 0) + 1
                return
            return super().hook(event, args)
    return NumericalReadPolicy
