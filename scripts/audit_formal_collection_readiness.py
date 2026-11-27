#!/usr/bin/env python3
"""Local audit only; never starts formal collection or imports a real Gate as data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from risk_agent.collection.formal_readiness import audit_formal_readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--gate-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    audit = audit_formal_readiness(args.root, args.gate_run)
    args.output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    path = args.output_dir / "formal_readiness.json"
    with path.open("x") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2); f.write("\n")
    with (args.output_dir / "artifact_manifest.json").open("x") as f:
        json.dump({"files": [{"path": path.name, "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}, f, indent=2); f.write("\n")
    print(json.dumps({k: v for k, v in audit.items() if k not in (
        "task_fault_control_inventory", "domain_fault_cells")}, ensure_ascii=False, indent=2))
    return 2 if audit["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
