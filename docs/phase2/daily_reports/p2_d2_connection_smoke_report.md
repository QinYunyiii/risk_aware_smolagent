# P2-D2 Connection Smoke Test — 90s Real WSL Validation

## Objectives

Validate the existing ConnectionLifecycle using two owned WSL OpenSSH forwards,
strict TLS, exactly four planned GET /v1/models requests at T+0/T+45, and a 90-second
observation window. Stop on any failure, without retry, reconnect, failover or generation.

## Status: BLOCKED

Both SSH forwards started successfully. Worker-0's first probe timed out during TLS
handshake. No HTTP response or model identity was received. The test stopped after
approximately 5.15 seconds of the observation window; Worker-1's GET, the T+45 probes
and the full 90-second window were not reached. This Run must not be repeated or
upgraded to PASS. The timeout's underlying cause remains UNDETERMINED.

## Local Environment Work

- Baseline HEAD/origin/main: `88a465f07f87705b08270c71c72c9b9e01cdf2dc`.
- Branch main; worktree clean before this task. Only the new Smoke entrypoint/tests
  were uncommitted during execution. The Run manifest binds their implementation
  hashes and the unchanged connection manager hash for reproducibility.
- WSL OpenSSH 9.6p1, dedicated audit identity and existing known_hosts.
- Both credential environment variables were present; the existing CA loaded into a
  strict SSL Context. Credential values were never printed or persisted by this task.
- Added an independent fixed-duration GET-only script which reuses ConnectionLifecycle;
  no connection manager, Agent or Checkpoint implementation was replaced.

### Approved SSH parameter substitution

The user explicitly approved `-F /dev/null` in place of `ClearAllForwardings=yes` for
this Smoke only. ClearAllForwardings would erase the desired explicit -L forwarding.
The actual child commands used `-F /dev/null` and did **not** set
`ClearAllForwardings=yes`. No claim is made that the original literal option was used.

Each child used the dedicated identity, port 4035, existing UserKnownHostsFile,
BatchMode=yes, IdentitiesOnly=yes, StrictHostKeyChecking=yes,
ExitOnForwardFailure=yes, ServerAliveInterval=15, ServerAliveCountMax=3,
ConnectTimeout=15, ControlMaster=no, ControlPath=none, -N and -T.
The two independent children each had one explicit -L:

| Worker | Owned local PID | Explicit LocalForward | Startup |
| --- | ---: | --- | --- |
| worker-0 | 956316 | `127.0.0.1:8000:127.0.0.1:8000` | READY |
| worker-1 | 956329 | `127.0.0.1:8001:127.0.0.1:8001` | READY |

The script read target/port/key/known_hosts from the expanded local Host configuration
and verified the original dedicated key fingerprint before launch. No historical
ControlMaster or occupied port was adopted. Each live Popen handle remained owned by
the manager, with distinct tunnel ID and process-start identity in connection_audit.

## Remote Environment Work

Two authorized SSH forwarding connections only. No remote shell command, service
change, GPU query/allocation/reset, vLLM launch/restart/stop, or remote file write was
performed. HTTP transport permits one bounded GET per scheduled probe with explicit
retries=0, no redirects, strict CA/hostname verification, connect timeout 5 seconds and
overall HTTPX timeout 10 seconds per operation.

No Agent, task T1--T5, Completion, Chat Completion, Checkpoint or Restore was executed.

## Work Completed

### TLS/API probe results

UTC timestamps are preserved in raw evidence. The observation window started at
`2026-11-24T17:40:12.421394+00:00` (2026-11-25 01:40:12 Asia/Shanghai).

| Planned probe | HTTP status | TLS / model identity | Latency |
| --- | --- | --- | ---: |
| T+0 worker-0 | null: no HTTP response | TLS handshake timeout; model unverified | 5.137 s |
| T+0 worker-1 | Not attempted | Not verified | — |
| T+45 worker-0 | Not attempted | Not verified | — |
| T+45 worker-1 | Not attempted | Not verified | — |

Worker-0 probe started at `17:40:12.427870+00:00` and ended at
`17:40:17.564583+00:00`. Error chain:

```text
ConnectTimeout: _ssl.c:983: The handshake operation timed out
  caused by ConnectTimeout: _ssl.c:983: The handshake operation timed out
    caused by TimeoutError: _ssl.c:983: The handshake operation timed out
```

The diagnostic retains infrastructure_failure / PROVIDER_REQUEST_TIMEOUT with
connection_failure_unknown and UNDETERMINED root cause. TLS was configured strictly,
but neither successful TLS negotiation nor a certificate validation failure was
observed. The raw TLS state therefore remains UNKNOWN; it was not promoted to PASS.

### Connection observations

- 250-ms monitoring was enabled while the observation window was active.
- Each Worker has 41 health observations across the existing monitor and Smoke
  controller: all showed PROCESS_ALIVE and LOCAL_PORT_LISTENING.
- There were 2 connection_created, 2 connection_ready, 1 request_started,
  1 request_failed and 1 failure-diagnosed event.
- Process-start identities remained equal to the captured owned identities.
- No connection_lost_observed event occurred. No unexpected SSH exit was observed.
- Both stderr files were empty (0 bytes).
- No TCP listener observation was interpreted as proof of TLS or Provider health.
- Full 90-second stability is **not verified** by this failed Run.

### Resource release

Only the Run-owned adapters were closed through ConnectionLifecycle.close(), which
uses adapter.close(expected_identity). No PID/name/port-based termination was used.

| Worker | Cleanup start (UTC) | Before | Cleanup end (UTC) | Exit code | Port |
| --- | --- | --- | --- | ---: | --- |
| worker-0 | 17:40:17.571163 | ALIVE | 17:40:17.576697 | 0 | CLOSED |
| worker-1 | 17:40:17.578426 | ALIVE | 17:40:17.583783 | 0 | CLOSED |

The observed exits followed explicit manager cleanup, rather than unexpected tunnel
loss. The manager is closed, its stderr handles flushed/fsynced, and an independent
local listener check confirmed ports 8000/8001 released. The script sent no remote
process-control command. Remote Worker survival after cleanup was not independently
probed; no extra GET or remote audit was added beyond the authorized request plan.

### Scope counters

| Item | Observed |
| --- | ---: |
| Owned SSH children created | 2 |
| Owned SSH children cleaned | 2 |
| GET probe attempts | 1 |
| Successful GET responses | 0 |
| Completion / Chat Completion requests | 0 / 0 |
| Automatic retries / reconnects / failovers | 0 / 0 / 0 |
| Frozen config changes | 0 |

## Files Changed

- `scripts/run_p2_d2_connection_smoke.py`
- `tests/test_connection_smoke.py`
- This sanitized report.

Frozen configuration SHA-256 before and after execution:

- Full Gate config: `3d3c5f161f9fa13c518e00a193eb54e74452a43c3ecb36f029d8df2986a5d3b4`
- Remote 1024 config: `4731cb56c128e3c150712745e25b9881fd8c19d64a37fd87a97f48ae3a5a57a5`

## Artifacts Generated

Unique ignored Run:
`runs/phase2_p2_d2_connection_smoke/p2_d2_connection_smoke_20261125_001/`

It contains run_manifest.json, connection_audit.jsonl, worker_probe_audit.jsonl,
two Worker stderr files, resource_release.json, gate_summary.json and artifact_manifest.json.
All 7 manifested artifacts were independently re-hashed: zero SHA-256 mismatches.
The manifest excludes itself. Historical Runs were not edited or resumed.
Raw connection metadata, stderr, credential files and connection specs are not
included in Git delivery.

## Commands and Tests

```text
.venv/bin/python -m pytest -q tests/test_connection_smoke.py
.venv/bin/python scripts/run_p2_d2_connection_smoke.py --authorize-remote --run-id p2_d2_connection_smoke_20261125_001
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

The 12 new collected local cases cover independent Worker identities, the fixed 90s
schedule and four-probe maximum, GET-only URL/method, absence of generation calls,
exit/port/identity/API/model failures, no retry/failover, HTTP failure audit, denied
authorization, artifact inclusion and cleanup after partial launch failure. Fake clocks
and adapters do not constitute real WSL stability evidence.

## Test Results

- Focused Smoke tests: 12 passed in 4.02s.
- Final full regression: 344 passed in 38.36s (baseline 332; 12 new cases).
- compileall and diff whitespace checks: PASS.
- Real Smoke: BLOCKED on first TLS handshake timeout.

## Acceptance Checklist

- [x] Approved SSH parameter substitution recorded accurately.
- [x] Dedicated identity, two independent owned children and two listeners verified.
- [x] Strict TLS configured; no verification bypass or fallback.
- [x] First failure stopped the plan without retry/reconnect/failover.
- [x] Owned resources released; raw evidence and hashes retained locally.
- [x] Frozen configurations and historical Runs preserved.
- [ ] Both Endpoint TLS handshakes complete.
- [ ] Both returned model identities verified.
- [ ] Four GET probes successful.
- [ ] Continuous 90-second observation complete.

## Known Issues

The first HTTPS probe timed out during TLS handshake while both local SSH processes
and listeners remained alive. The root cause is not established. This result alone
does not justify a connection architecture redesign or a claim that either remote
Worker crashed.

## Remaining Work

Do not enter the full five-task Real-Qwen Gate from this result. Any further connection
diagnostic or new Smoke attempt requires separate authorization and a new Run ID.
Do not retry this Run, execute T1--T5, start P2-D3 or collect formal data.

## Git Commit

Delivery message: `test: add P2-D2 connection smoke validation`.
Only the entrypoint, synthetic tests and sanitized report are included.
