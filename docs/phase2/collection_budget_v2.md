# Protocol v2 Collection Budget

> Budget status: planning estimate, not authorization to start GPU/Qwen work.
> Current B1 resource use: 0 GPU, 0 Qwen calls, 0 Docker/SSH/API operations.

## 1. Measured baseline

The audited Day 8 run contains 20 token-complete attempts with 70,333 total observed Tokens
(mean 3,516.7), mean attempt latency 10.86 seconds, and about 1.1 MB of run artifacts. Those were
mostly single-step tasks and do not directly predict 3--8-step v2 costs. Local disk currently has
about 945 GiB available.

## 2. Planned workload

| Item | Target | Minimum/Gate | Notes |
|---|---:|---:|---|
| Source task attempts | 160 | 120 valid captures | 96/32/32 target by split |
| Primary Checkpoints | 160 | 120 | one per task; 72/24/24 minimum |
| CONTINUE branches | 160 | one per valid Checkpoint | risk phase only |
| RETRY/REPLAN branches | 0 | 0 | explicitly deferred |
| Independent repeats | 20% of selected combinations | descriptive | separate attempt IDs; not unique Checkpoints |
| Safe infra reattempt reserve | up to 10% planning reserve | protocol-limited | only before side effects; never hides failed records |

The source execution performs the first model/tool step and stops at capture. CONTINUE performs
the remaining steps, so the pair approximates one full model-backed execution rather than two
full reruns.

## 3. Request, Token, and tool estimates

Assumptions:

- existing 40 templates x 2 instances: one tool call plus final response, about two model
  requests/task;
- new 40 templates x 2 instances: 3--8 dependent tool steps plus final response, about 4--9
  model requests/task;
- deviations, correction calls and safe pre-side-effect infrastructure reattempts add reserve;
- hard per-task limits are 10 model calls, 12 tool calls and 50,000 cumulative model Tokens.

| Resource | Expected planning range | Hard/contingency bound |
|---|---:|---:|
| Qwen API model requests | 480--880 | 1,600 across 160 task pairs |
| Requests including 10% safe infra reserve | about 530--970 | reserve is not automatic after side effects |
| Tool calls from reference complexity | 320--720 | 1,920 protocol cap |
| Operational tool-call expectation | 400--1,200 | includes correction/no-progress behavior |
| Total model Tokens | 2.4--6.4 million | conservative planning ceiling 10.5 million |

Actual usage is measured, never inferred from the bounds. Missing model usage is `null` with a
status; it is not zero. If context truncation or the 8,192-token context limit would alter the
next input, that task is invalid under rc1 rather than silently summarized.

## 4. Time and GPU budget

| Stage | Remote GPU | Estimated service time | Local/elapsed time |
|---|---|---:|---:|
| B1 protocol | none | 0 | local document work only |
| B2 local implementation/tests | none | 0 | 2--4 engineering days estimate |
| B2/B3 five-Checkpoint Gate | at most 2 free RTX 4090 | 1--3 hours | includes preflight and cleanup |
| B4 8--12-task smoke | at most 2 free RTX 4090 | 2--4 hours | separate non-formal run |
| 160-task formal collection | at most 2 free RTX 4090 | 6--12 wall-clock service hours | likely 3--5 calendar days with shared-resource queues |

At two Workers the formal estimate corresponds to at most about 12--24 GPU-card-hours. This is
an upper planning allocation, not permission to occupy GPUs. Before every run, GPU availability
must be checked twice and reconciled with lab scheduling; an empty process table inside the
container is insufficient. No process may be killed or GPU reset. TLS remains enabled.

## 5. Local disk and artifact budget

Reserve 5--10 GB for Checkpoint copies, SQLite backups, canonical messages, raw model/tool events,
branch workspaces, manifests and audit reports. Source JSONL is append-only. Branch workspaces may
be compacted only under a separately approved retention policy; B1 authorizes no deletion.

Preflight requires at least 20 GB free to keep a 2x safety margin over the planned reserve.

## 6. Stop conditions

Stop the batch on any of:

- no approved free GPU/Worker mapping;
- TLS/model identity mismatch;
- Restore or first-input hash mismatch;
- cross-branch state mutation;
- unversioned Prompt/tool/verifier/catalog change;
- disk free space below 20 GB;
- repeated model-service instability beyond the one safe pre-side-effect reattempt;
- a write with committed/unknown outcome;
- critical Token/latency observability below 95%;
- feature provenance or split-isolation violation.

Shared-GPU queue time and unexpected model latency can widen calendar time. The budget must be
updated from B4 measurements before formal collection, without changing labels or split rules.
