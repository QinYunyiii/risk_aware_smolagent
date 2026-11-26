#!/usr/bin/env python3
"""Default offline preflight; remote mode requires independent Stage B authorization."""
from __future__ import annotations

import argparse
import json
from risk_agent.collection.remote_local_tls_isolation import offline_preflight
from risk_agent.collection.tls_stage_b import cli_main as production_main


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=('preflight','remote_tls_diagnostic'),default='preflight')
    parser.add_argument('--authorize-stage-b',action='store_true')
    parser.add_argument('--run-id')
    args=parser.parse_args(argv)
    if args.mode=='preflight':
        result=offline_preflight()
        print(json.dumps(result,indent=2))
        return 0 if result['stage_a_gate']=='REMOTE_TLS_ISOLATION_STAGE_A_PASS' else 1
    if not args.authorize_stage_b or not args.run_id:
        raise RuntimeError('Separate Stage B authorization and unique run ID required')
    # Historical API remains importable; executable Stage B always uses the
    # complete raw-evidence production path, never the legacy summary runner.
    return production_main(['--mode','execute','--authorize-stage-b','--run-id',args.run_id])


if __name__=='__main__':
    raise SystemExit(main())
