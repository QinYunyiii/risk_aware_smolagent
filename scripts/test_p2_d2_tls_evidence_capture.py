#!/usr/bin/env python3
"""Offline-only Stage A entry point. No SSH/OpenSSL executable modes."""
from __future__ import annotations

import argparse
import json

from risk_agent.collection.tls_evidence_capture import offline_preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['offline_preflight'], default='offline_preflight')
    parser.parse_args(argv)
    print(json.dumps(offline_preflight(), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
