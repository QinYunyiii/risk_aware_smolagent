#!/usr/bin/env python3
"""Build the local-only P2-D1 catalog acceptance artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from risk_agent.benchmark import build_p2_d1_catalog


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-templates",
        type=Path,
        default=Path("configs/task_templates.yaml"),
    )
    parser.add_argument(
        "--templates",
        type=Path,
        default=Path("configs/task_templates_v2.yaml"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/phase2_catalog"),
    )
    parser.add_argument(
        "--run-id",
        default=f"p2_d1_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    output = args.output_root / args.run_id
    summary = build_p2_d1_catalog(
        base_templates_path=args.base_templates,
        v2_templates_path=args.templates,
        output_directory=output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
