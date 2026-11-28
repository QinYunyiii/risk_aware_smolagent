#!/usr/bin/env python3
"""Offline-only qualified data snapshot. Does not fit or evaluate models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from risk_agent.benchmark.schema import validate_identifier
from risk_agent.collection.day5_gate import sha256_file
from risk_agent.collection.formal_contract import write_once_json
from risk_agent.prediction.formal_preparation import prepare_rows


def main(argv=None):
    try:
        from scripts.run_frozen_continue_acceptance import _offline_guard
    except ModuleNotFoundError:
        from run_frozen_continue_acceptance import _offline_guard
    _offline_guard()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--proof", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("runs/phase2_baseline_preparation"))
    args = parser.parse_args(argv)
    validate_identifier(args.run_id, "run_id")
    root = args.root.resolve()
    directory = (root / args.output_root / args.run_id).resolve()
    if not directory.is_relative_to(root / "runs"):
        raise ValueError("Preparation output must remain in local run storage")
    rows, audit = prepare_rows(root, args.proof)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    for split in ("train", "validation"):
        with (directory / (split + ".jsonl")).open("x", encoding="utf-8") as handle:
            for row in rows:
                if row["split"] == split:
                    handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
    write_once_json(directory / "audit_summary.json", {"run_id": args.run_id, **audit})
    write_once_json(directory / "source_proof.json", json.loads(args.proof.read_text()))
    write_once_json(directory / "artifact_manifest.json", {"files": [
        {"path": str(path.relative_to(directory)), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(directory.rglob("*")) if path.is_file()]})
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
