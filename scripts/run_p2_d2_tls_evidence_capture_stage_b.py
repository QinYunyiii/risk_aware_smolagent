#!/usr/bin/env python3
"""Authoritative TLS Stage B entry. Default is local preparation, never execution."""
from risk_agent.collection.tls_stage_b import cli_main as main


if __name__ == '__main__':
    raise SystemExit(main())
