#!/usr/bin/env python3
"""Offline integrity check only: no collection, credentials or network access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from risk_agent.collection.protocol_freeze import (
    ORIGINAL_REVISION, READONLY_REFRESH_REVISION, load_frozen_collection,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--revision", choices=(ORIGINAL_REVISION, READONLY_REFRESH_REVISION),
                        default=READONLY_REFRESH_REVISION)
    args = parser.parse_args()
    print(json.dumps(load_frozen_collection(
        args.root, implementation_revision=args.revision
    ).audit_summary(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
