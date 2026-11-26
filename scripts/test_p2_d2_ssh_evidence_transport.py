#!/usr/bin/env python3
"""No SSH execution mode: real transport requires separately authorized Stage B."""
import argparse
import json

from risk_agent.collection.ssh_evidence_transport import offline_preflight


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['offline_preflight'], default='offline_preflight')
    parser.parse_args(argv)
    print(json.dumps(offline_preflight(), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
