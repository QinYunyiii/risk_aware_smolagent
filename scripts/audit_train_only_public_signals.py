#!/usr/bin/env python3
"""P2-D3 offline audit. No mixed snapshot, model, future outcome or network."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


def install_guard(root: Path, proof: Path, output: Path, events: list[dict]) -> None:
    """Guard real process reads as well as explicit loader APIs."""
    from risk_agent.prediction.train_evidence import permitted_member
    root, proof, output = root.resolve(), proof.resolve(), output.resolve()
    runroot = root / "runs"
    allowed_roots = (runroot / "phase2_formal_train", runroot / "phase2_formal_train_continuation")
    def check(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind"}:
            events.append({"event": event, "blocked": True})
            raise RuntimeError("Offline Train audit forbids network operations")
        if event == "subprocess.Popen" and Path(str(args[0])).name in {"ssh", "openssl", "curl", "wget", "docker"}:
            events.append({"event": event, "blocked": True})
            raise RuntimeError("Offline Train audit forbids remote tools")
        if event != "open" or not isinstance(args[0], (str, bytes)):
            return
        path = Path(args[0]).resolve()
        if path.is_relative_to(root / "docs/phase2/daily_reports"):
            events.append({"event": "forbidden_dataset_read", "path": str(path.relative_to(root)), "blocked": True})
            raise RuntimeError("Historical reports cannot be used for Train-only signal design")
        if not path.is_relative_to(runroot) or path == proof or path.is_relative_to(output):
            return
        allowed = False
        for parent in allowed_roots:
            if path.is_relative_to(parent):
                parts = path.relative_to(parent).parts
                name = "/".join(parts[1:])
                allowed = permitted_member(name) or bool(re.fullmatch(r"artifact_manifest(?:_[a-f0-9]+)?\.json", name))
        if not allowed:
            events.append({"event": "forbidden_dataset_read", "path": str(path.relative_to(root)), "blocked": True})
            raise RuntimeError("Only explicitly public/monitor Train decision sources may be opened")
    sys.addaudithook(check)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--train-proof", type=Path, required=True)
    p.add_argument("--proof-sha256", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / "runs/phase2_train_only_public_signal_audit") or output.exists():
        raise ValueError("Audit needs a new independent local run directory")
    events = []
    install_guard(root, args.train_proof, output, events)
    from risk_agent.prediction.train_evidence import load_train
    from risk_agent.prediction.train_signal_audit import build_audit
    rows, proof = load_train(root, args.train_proof, args.proof_sha256)
    existing, candidates, computed = build_audit(rows)
    failures = sum(r["y_failure"] for r in rows)
    # An actual forbidden dataset access always invalidates the audit, even if a
    # library catches the raised error. Local IPv6 capability probes are separate.
    if any(e["event"] == "forbidden_dataset_read" for e in events):
        raise RuntimeError("Forbidden dataset access attempted")
    summary = {"gate": "PASS", "audit_id": output.name, "train_rows": len(rows),
        "train_successes": len(rows) - failures, "train_failures": failures,
        "train_templates": len({r["template_id"] for r in rows}), "censored_rows": len(proof["censored_task_ids"]),
        "constant_features": existing["constant_features"], "candidate_count": len(candidates["candidates"]),
        "candidate_recommendations": {c["feature_name"]: c["recommendation"] for c in candidates["candidates"]},
        "model_fit_count": 0, "new_collection_count": 0, "validation_reads": 0, "test_reads": 0,
        "future_data_used": 0, "hidden_metadata_used": 0, "source_traceability_percent": 100,
        "read_guard_events": events, "proof": proof}
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    for name, value in (("summary.json", summary), ("existing_features.json", existing),
                        ("candidate_features.json", candidates), ("decision_signal_rows.json", computed)):
        with (output / name).open("x") as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False, indent=2); f.write("\n")
    entries = [{"path": f.name, "size_bytes": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
               for f in sorted(output.iterdir()) if f.is_file()]
    with (output / "artifact_manifest.json").open("x") as f:
        json.dump({"files": entries}, f, indent=2); f.write("\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "proof"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
