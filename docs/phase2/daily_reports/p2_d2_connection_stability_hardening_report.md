# P2-D2 Connection Stability Hardening — Stage A

## Objectives

Audit the failed five-task Run, establish connection ownership, add durable request-linked
connection observation, and validate safe stopping/cleanup using local simulations only.

Baseline: `3fa9c005155d8300041b9beb1ddcaf66f48b5f65`, main, clean worktree,
matching local origin/main at start. Date: 2026-11-25 Asia/Shanghai.

## Local Environment Work

Read the repository instructions, project/next-stage guides, Stage A/B reports and the
connection/client implementations. Inspected local SSH configuration and `ssh -V` only.
Installed OpenSSH is `9.6p1 Ubuntu-3ubuntu13.20`. No Windows script exists in the repository.

### A. Historical forensics

Authoritative Run: `p2_d2_full_gate_1024_20261124_001` under the ignored
`runs/phase2_p2_d2_full_gate_1024/` directory.

Both manifests were re-hashed from local bytes: original 87 entries and post-audit 89
entries, zero mismatches. No historical record was rewritten or appended.

| Evidence | Finding | Confidence |
| --- | --- | --- |
| Provider response audit, last record | T2 Restore APIConnectionError, transport_failure, null Usage | CONFIRMED |
| Request timestamp | 2026-11-24T08:07:12.679968+00:00 | CONFIRMED recorded timestamp |
| Failure timestamp | 2026-11-24T08:08:14.622224+00:00 | CONFIRMED recorded timestamp |
| Monotonic latency | 64.18428585899528 seconds | CONFIRMED recorded duration |
| Exception chain | Only APIConnectionError / “Connection error.” persisted; underlying cause absent | NOT OBSERVABLE |
| SSH exit timestamp/code/stderr | No continuous connection event log or owned child handle in this Run | NOT OBSERVABLE |
| Post-run release record | Sockets/listeners absent when checked later | CONFIRMED observation |
| Final remote sample in release record | Workers remained alive at 14:40:33 UTC | CONFIRMED recorded observation |
| Loss of forwarding as contributing factor | Consistent with later missing listeners | LIKELY, not proven |
| Parent/terminal cleanup, network interruption, WSL/Windows lifecycle | No discriminating evidence | POSSIBLE |
| Specific SSH exit cause and order relative to Provider failure | Cannot reconstruct | UNDETERMINED |

The wall timestamps differ by about 61.94 seconds whereas the monotonic latency is
64.18 seconds. The old records do not explain the difference; they must not be used to
invent a precise SSH failure timeline. The previous Stage B report's “disappeared before
T2's Restore request completed” statement is stronger than the retained evidence.
This report corrects that interpretation without editing the historical report or Run.
Infrastructure failure remains confirmed; direct SSH causation remains unconfirmed.

### B. Connection architecture audit

| Responsibility | Historical Run / existing implementation | New managed full Gate |
| --- | --- | --- |
| Start | External session commands, recorded in prior conversation/release record | CLI owns ConnectionLifecycle and two SSHProcess adapters |
| Hold alive | Detached `ssh -f -M`; no Gate-owned process handle | Foreground `Popen`, independent session, retained through entire Gate |
| Process observation | After-the-fact socket/PID checks | Child-handle poll every 250 ms plus request boundaries |
| stderr / exit code | Not retained for historical Run | Private per-Worker stderr file and observed exit code |
| Cleanup | External commands | CLI finally, after requests finish and before artifact manifest |
| Worker separation | Two separate ControlMasters | Two independent foreground OpenSSH children |
| Windows ownership | Earlier T1 used external Windows OpenSSH | Adapter contract only; Windows native behavior NOT VERIFIED |

The repository already has `scripts/manage_remote_vllm.py`, but it was not the owner
of the historical full Gate's temporary sockets. Its `tunnel_start` uses `-f -M -S`,
stores only a PID file and checks local ports; `_close_control` trusts a responding
Control Socket and unlinks it. It has no Run identity, request-linked exit monitor,
or retained child stderr/return code. It remains a legacy deployment tool and is not
invoked by the new full Gate. No evidence proves it closed the historical connections.

`run_p2_d2_full_gate_1024.py` previously called API preflight and the Runner without
owning any tunnel. `agent_factory.py` creates TLS-verified clients with HTTP automatic
retries zero; `RecordingModel` performs Provider delegation and audit; WorkerRouter
offers failover for other callers but the frozen Gate does not invoke it.
`check_remote_api.py` has a separate bounded ordinary-chat retry helper which is not
called by this Gate. No Windows process manager exists in this repository.
The local SSH config contains only the GitHub deploy-key alias, with no experiment
ControlPersist/forwarding settings. Neither idle expiry nor premature external cleanup
can be established from that configuration.

## Remote Environment Work

None. SSH connections, actual tunnels, remote HTTPS/Provider requests, GPU operations,
vLLM operations and Docker operations: all zero. GitHub push is the only planned
external delivery operation.

## Work Completed

### C. Minimal implementation

- Added `monitoring/connection_lifecycle.py`: ownership metadata, process adapters,
  foreground OpenSSH, append-only UTF-8/fsync JSONL, periodic observation, request
  lineage, separate diagnostic classification, and guarded cleanup.
- `ConnectionIdentity` binds Run, Worker, tunnel ID, platform, port, target, live child
  PID, handle-generation token, launch timestamp, owner and cleanup responsibility.
  Native cleanup uses the retained Popen child handle, never a discovered port/PID or
  an adopted Control Socket. Foreign identity changes are rejected. No socket unlink
  or process-name kill is used. Existing occupied ports cause startup refusal.
- `RecordingModel` reports request start through ProviderResponseAudit. Provider
  response/failure persistence invokes the finish observation, including exception
  chains and local request ID. Observability is external to canonical Agent state.
- The full Gate now requires private `--connection-spec` for real mode after the
  existing authorization/evidence checks. It owns the manager through API preflight,
  all tasks, and final cleanup. Local preflight never launches connections.
- Direct Runner tests without a manager explicitly record UNKNOWN rather than
  fabricate process/port evidence. Managed runs observe real local child/socket state.
- Source API failures and restored HTTP API errors consistently retain the research
  category infrastructure_failure. They cannot become model_behavior_failure merely
  because the Agent framework wrapped the error.
- Diagnostics distinguish observed SSH exit, closed listener, TLS, read timeout,
  Provider connection/HTTP errors and unknown failure. Evidence confidence and root
  cause confidence are separate; observed exit never automatically proves causation.

### SSH option changes and limits

Previous externally launched Masters used `-f -M -S`; the candidate uses `-N -T`,
`start_new_session=True`, `ControlMaster=no`, `ControlPath=none`, and no `-f`.
This gives the Run a waitable child handle, preserves stderr/exit status and removes
shared Control Socket cleanup from this path. `-F /dev/null` avoids unrelated forwards.
Keepalive values remain ServerAliveInterval=15 and ServerAliveCountMax=3, as in the
existing manager. ConnectTimeout remains 15 seconds; no API timeout was increased.
ExitOnForwardFailure and strict host-key verification remain enabled. No ControlPersist,
server-side setting, Windows global setting, TCP keepalive tuning or TLS relaxation
was added. These options are supported by the inspected local OpenSSH version.

An independent session addresses terminal process-group coupling but is not immunity
to machine shutdown, network loss or an external process supervisor. Readiness observes
a local listener only; subsequent existing strict TLS/authenticated preflight remains
required. A 250-ms poll timestamps observation, not the exact kernel exit instant.

### Windows event interface

An external Windows owner may implement ProcessAdapter with the same immutable
ConnectionIdentity, snapshot() and close(expected_identity) contract. Snapshot returns
process_state, listener_state, exit_code and source; it must use Windows-native process
start identity and native PID, not reinterpret WSL PIDs. Requests carry the same local
Provider audit ID. The existing Fake Adapter validates these boundaries only.
There is no native Windows launcher/bridge in this change; Windows production support
and actual parent-termination behavior are NOT VERIFIED. No outside script was edited.

## Files Changed

- `src/risk_agent/monitoring/connection_lifecycle.py`
- `src/risk_agent/monitoring/provider_response_audit.py`
- `src/risk_agent/agent/recording_model.py`
- `src/risk_agent/collection/p2_d2_real_qwen_gate.py`
- `scripts/run_p2_d2_full_gate_1024.py`
- `tests/test_connection_lifecycle.py`
- `tests/test_p2_d2_full_gate_1024.py`
- This report.

## Artifacts Generated

Synthetic artifacts only in pytest temporary directories. New managed Runs retain
connection_audit.jsonl and private Worker SSH stderr alongside Provider audit, all
covered by the final artifact manifest after cleanup. Historical Run remains unchanged.
Connection events contain no credential values or sensitive environment dump.

## Commands and Tests

```text
.venv/bin/python -m pytest -q tests/test_connection_lifecycle.py tests/test_p2_d2_full_gate_1024.py tests/test_p2_d2_real_qwen_gate.py tests/test_p2_d2_completion_budget_revision.py
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

### D. Fault injection coverage

| Requested case | Test evidence / expected and observed outcome |
| --- | --- |
| Stable lifecycle | test_stable_lifecycle_and_active_cleanup_guard: append events, deny early cleanup, close owned adapter |
| Exit during request | test_exit_observed_during_request_has_code_and_request_lineage: observe 255, timestamp and request ID |
| Alive process, closed port | test_process_and_listener_are_separate_observations: local_forward_unavailable |
| Listening port, failed Provider | Same parameterized test: provider_connection_failure |
| Read timeout | test_t2_restore_transport_failure_preserves_checkpoint_and_stops[read_timeout]: null Usage, six attempts, stop |
| TLS failure | Same test [tls]: preserved nested TLS cause, no fallback |
| Time ordering A/B/C | test_temporal_observations_do_not_claim_causality: three scenarios, no root-cause claim |
| Worker 1 Restore failure | Same T2 transport test: T1 and T2 Checkpoints retained, no T3--T5, no failover |
| Cleanup safety | test_cleanup_never_targets_foreign_identity, test_existing_port_never_adopted_or_cleaned: refuse foreign identity/port |
| Windows/WSL | test_platform_adapter_identity_and_parent_exit_simulation: fake platform/PID separation only |
| Five-task success | Existing full synthetic Gate test: 15 Provider requests, 5 Checkpoints, six hashes/input/replay 5/5 |
| Audit isolation | Full synthetic Gate checks external events, no connection_audit in checkpoint JSON; existing frozen identity/budget tests retained |
| Partial launch failure | test_two_worker_launch_and_partial_startup_cleanup: clean owned first Worker after second launch fails |

### E. Protocol isolation

No file under configs/ or checkpoint/ changed. Frozen model, full Gate, historical
256-token and T1-1024 configs and Catalog retain their original bytes. Source/Restore
budget checks remain unchanged. Observation metadata never enters model kwargs,
Agent Memory or RuntimeState. Six Hash computations and Checkpoint v2 schema are
untouched. Synthetic all-five tests retain 15 Provider requests, fixed Worker schedule,
Replay Guard and Source/Restore counters. Automatic Retry and Failover stay zero;
formal_experiment/research_dataset_eligible remain false and intervention labels null.

## Test Results

- Added 24 collected cases: 20 connection lifecycle cases and 4 T2 Restore Provider
  fault variants. Baseline 308 tests increased to 332.
- Focused connection/P2-D2 regression: 59 passed in 9.24 seconds.
- Full regression: 332 passed in 34.78 seconds, no skips or xfails.
- compileall and git diff --check: PASS.
- Final re-hash of historical 87/89 manifests: zero mismatches.
- No Stage A SSH, remote API, Qwen, GPU, vLLM or Docker operation occurred.

## Acceptance Checklist

- Ownership and audit gaps identified with code and retained evidence.
- Managed process handles, stderr, exit status, request linkage and safe cleanup tested.
- Infrastructure diagnostics independent from research labels and Checkpoint state.
- T2 Restore fault injection preserves prior results and stops the Gate.
- Synthetic five-task normal path retains all frozen acceptance checks.
- Historical manifests reverified; no historical record altered.
- Real SSH exit cause and native Windows lifecycle explicitly unverified.

## Known Issues

The historical SSH exit cause remains UNDETERMINED. This implementation improves
ownership and evidence collection; it does not prove the old failure was repaired.
Native Windows support requires its own reviewed adapter and real validation.
The existing legacy deployment tunnel manager retains its old semantics and should
not be used to adopt/clean the managed Gate's connections.

## Remaining Work

### G. Next authorized real validation

Before a new real attempt: obtain separate authorization and a unique Run ID; verify
both existing Workers' ownership, dynamic GPU mapping/occupancy and model hashes;
verify SSH identity and strict TLS/authentication; confirm local forwarding ports free;
provide a private JSON connection spec with exactly worker-0/worker-1 entries, each
containing explicit target, ssh_port, identity_file, known_hosts_file, local_port and
remote_port (8000/8001 respectively). Do not store this private file in Git.

Use the full Gate's new --connection-spec with its existing independent authorization
and remote evidence arguments. The manager owns the two new local children through
the whole Run. Review readiness events, continuous health and linked request events;
on failure, keep null Usage and stop without reconnect/resend. Check final child exit
codes, stderr, cleanup events and manifest SHA-256. Validate actual WSL parent/session
behavior separately; Windows remains outside the supported native launch path.

Local synthetic capabilities are ready for a separately authorized connection
validation. The real five-task Gate remains BLOCKED and is not automatically rerun.

## Status: CONDITIONAL PASS

Local engineering observability and safe-stop coverage passed; the historical SSH exit
cause and real platform stability remain unverified. No new Real-Qwen Gate was run.

## Git Commit

Baseline above; delivery message: `fix: harden P2-D2 connection lifecycle and failure auditing`.
Only code, synthetic tests and this sanitized report are eligible for delivery.
