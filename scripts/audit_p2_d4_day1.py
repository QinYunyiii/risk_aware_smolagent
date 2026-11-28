#!/usr/bin/env python3
"""Launch with .venv/bin/python -I -S -B; explicit policy, no site startup."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import os
import subprocess
import sys
import uuid


def main(argv=None):
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise RuntimeError("Use python -I -S -B to disable environment/site startup and cache writes")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    # Import the stdlib-only bootstrap directly: prediction/__init__.py eagerly
    # imports third-party packages and must not run before the guard.
    spec = importlib.util.spec_from_file_location("_p2d4_access_bootstrap",
        root / "src/risk_agent/prediction/train_only_access.py")
    access = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = access
    spec.loader.exec_module(access)
    ReadPolicy, support_inventory, sha256, strict_path = (
        access.ReadPolicy, access.support_inventory, access.sha256, access.strict_path)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile-sha256", required=True)
    p.add_argument("--run-id")
    p.add_argument("--runtime-preflight", action="store_true")
    args = p.parse_args(argv)
    run_id = args.run_id or "p2_d4_day1_" + uuid.uuid4().hex[:12]
    if not run_id.replace("_", "").isalnum():
        raise ValueError("Invalid Run ID")
    output = strict_path(root / "runs/phase2_p2_d4_day1" / run_id)
    status = subprocess.check_output(["git", "status", "--short"], cwd=root).decode()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    if status and not args.runtime_preflight:
        raise RuntimeError("Formal offline audit must use a clean implementation Commit")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    history = {str(f.relative_to(root)): [f.stat().st_size, f.stat().st_mtime_ns]
        for f in (root / "runs").rglob("*") if f.is_file() and not f.is_relative_to(output)}
    extra = [root / n for n in ("src/risk_agent/prediction/train_only_access.py",
        "src/risk_agent/prediction/train_day1_audit.py", "src/risk_agent/prediction/train_day1_runtime.py",
        "scripts/audit_p2_d4_day1.py")]
    support = support_inventory(root, extra, output / "absent_import_cache")
    names = {"summary.json", "access_policy.json", "access_audit.json", "run_context.json",
             "integrity.json", "artifact_manifest.json"}
    policy = ReadPolicy(output=output, support=support, outputs={str(output / n) for n in names})
    profile_path = root / "configs/p2_d4_day1_access_v1.json"
    policy.enroll(profile_path, args.profile_sha256)
    # No credentials are needed for this audit; do not inherit client credentials
    # into data-loading imports, and never serialize environment values.
    for name in tuple(os.environ):
        if any(token in name.upper() for token in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")):
            del os.environ[name]
    policy.install()
    result = {"gate": "BLOCKED"}
    error = None
    try:
        profile = policy.json(profile_path)
        if profile["revision"] != "p2-d4-day1-train-access-v1":
            raise ValueError("Unknown access profile")
        for relative, expected in profile.get("frozen_context_sha256", {}).items():
            context_path = strict_path(root / relative)
            if not context_path.is_relative_to(root):
                raise ValueError("Frozen context file escaped project")
            policy.enroll(context_path, expected)
            policy.verify(context_path)
        runtime_spec = importlib.util.spec_from_file_location("_p2d4_pure_runtime",
            root / "src/risk_agent/prediction/train_day1_runtime.py")
        runtime = importlib.util.module_from_spec(runtime_spec)
        runtime_spec.loader.exec_module(runtime)
        runtime.prepare_runtime(root, profile["frozen_reuse_sha256"], access)
        from risk_agent.prediction.train_day1_audit import run_day1
        if args.runtime_preflight:
            from risk_agent.agent.agent_state_adapter import canonical_hash
            from risk_agent.prediction.formal_preparation import feature_projection
            assert canonical_hash({}) and callable(feature_projection)
            policy.seal()
            policy.assert_clean()
            result = {"gate": "PREFLIGHT_PASS", "train_data_loaded": False}
        else:
            result = run_day1(policy, root, profile)
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        result = {"gate": "BLOCKED", "error": error}
    changed = [n for n, old in history.items() if not (root / n).is_file()
        or [(root / n).stat().st_size, (root / n).stat().st_mtime_ns] != old]
    if changed or policy.events:
        result["gate"] = "BLOCKED"
    integrity = {"historical_files_metadata_checked": len(history), "historical_metadata_changes": changed,
        "other_history_content_hashed": False, "boundary": "strict_no_future_or_hidden_file_bytes"}
    policy_doc = {"sealed": policy.sealed, "inputs": dict(policy.inputs), "support": dict(policy.support),
        "declared_outputs": sorted(policy.outputs), "unknown_reads": "deny", "unknown_writes": "deny",
        "source": "exact files, not directory grants or extension-only checks"}
    context = {"run_id": run_id, "implementation_commit": head, "initial_git_status": status,
        "argv": sys.argv, "profile_sha256": args.profile_sha256, "isolated": True, "no_site": True,
        "mode": "runtime_preflight" if args.runtime_preflight else "offline_day1_audit",
        "implementation_file_sha256": {str(f.relative_to(root)): policy.support[str(f)] for f in extra}}
    policy.write_json("access_policy.json", policy_doc)
    policy.write_json("access_audit.json", {"blocked_events": policy.events, "allowed_read_counts": policy.reads})
    policy.write_json("run_context.json", context)
    policy.write_json("integrity.json", integrity)
    policy.write_json("summary.json", result)
    # Output checksums are calculated in memory from the exact serialization used
    # above; reading arbitrary files from the owned directory is not permitted.
    values = {"access_policy.json": policy_doc,
        "access_audit.json": {"blocked_events": policy.events, "allowed_read_counts": policy.reads},
        "run_context.json": context, "integrity.json": integrity, "summary.json": result}
    entries = []
    for name, value in values.items():
        data = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
        entries.append({"path": name, "size_bytes": len(data), "sha256": sha256(data)})
    policy.write_json("artifact_manifest.json", {"files": entries})
    print(json.dumps({"run_id": run_id, "gate": result["gate"], "error": error,
        "blocked_events": policy.events, "output": str(output)}, ensure_ascii=False))
    return 0 if result["gate"] in {"PASS", "PREFLIGHT_PASS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
