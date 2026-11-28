"""Reuse hash-pinned pure code without eager package/model/client imports."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import types
from typing import Any


def prepare_runtime(root: Path, pinned: dict[str, str], access: types.ModuleType) -> None:
    """Only import wiring changes in this worker; original function ASTs do not.

    The canonical functions and feature_projection are pure exports of otherwise
    heavy modules. Extract their exact AST nodes; do not reimplement or rewrite
    their bodies. The complete feature extractor and Train loader run unchanged.
    """
    texts = {}
    for name, expected in pinned.items():
        data = (root / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Frozen pure-code reuse hash mismatch")
        texts[name] = data.decode()
    for name in ("risk_agent", "risk_agent.agent", "risk_agent.collection", "risk_agent.prediction"):
        module = types.ModuleType(name)
        module.__path__ = [str(root / "src" / name.replace(".", "/"))]
        module.__package__ = name
        sys.modules[name] = module
    sys.modules["risk_agent.prediction.train_only_access"] = access

    def pure(name, file, wanted, globals_):
        tree = ast.parse(texts[file], filename=file)
        nodes = []
        found = set()
        for node in tree.body:
            identifier = node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else (
                node.targets[0].id if isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) else None)
            if identifier in wanted:
                found.add(identifier); nodes.append(node)
        if found != set(wanted):
            raise ValueError("Required frozen exports missing")
        tree.body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes]
        ast.fix_missing_locations(tree)
        module = types.ModuleType(name)
        module.__dict__.update(globals_)
        module.__file__ = str(root / file)
        sys.modules[name] = module
        exec(compile(tree, str(root / file), "exec"), module.__dict__)
        return module

    adapter = pure("risk_agent.agent.agent_state_adapter", "src/risk_agent/agent/agent_state_adapter.py",
        {"canonical_json", "canonical_hash"}, {"json": json, "hashlib": hashlib, "Any": Any})

    def whole(name, relative):
        spec = importlib.util.spec_from_file_location(name, root / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    features = whole("risk_agent.collection.formal_features", "src/risk_agent/collection/formal_features.py")
    pure("risk_agent.prediction.formal_preparation", "src/risk_agent/prediction/formal_preparation.py",
        {"ERROR_TYPES", "feature_projection"}, {"NAMES": features.NAMES, "canonical_hash": adapter.canonical_hash,
                                                "Any": Any, "math": math})
    whole("risk_agent.prediction.train_evidence", "src/risk_agent/prediction/train_evidence.py")
