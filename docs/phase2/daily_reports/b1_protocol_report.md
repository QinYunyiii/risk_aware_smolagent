# Workstream B - B1 Protocol Report

## Objectives

Freeze a review candidate for real Qwen CONTINUE risk-data collection: decision point, tri-state
label, failure taxonomy, F0--F6 behavior, feature provenance policy, task/split/scale plan,
Checkpoint and raw-data schemas, evaluation/statistics, resource budget, and B2--B4 Gates.

B1 is protocol design and code/data audit only. It does not implement B2, start Qwen/GPU, collect
formal data, retrain models, or modify historical artifacts.

## Current Baseline

- Git HEAD at start: `a63d5e0 audit: complete P1-D2 risk feature shortcut analysis`.
- Python 3.12.3, smolagents 1.26.0, openai 3.52.0.
- P1-D1/D2/D3 audit checks reproduce successfully.
- B0 audit PASS, while the real formal data chain remains blocked on complete Agent Restore and
  model-backed CONTINUE.
- Existing catalog: 40 templates/200 instances, 24/8/8 templates and 120/40/40 instances.
- All 200 current catalog instances contain exactly one reference step.
- Current formal model-backed risk examples: 0.
- Start-of-turn user files were untracked and preserved; no existing B1 target file existed.

## Local Environment Work

Read the required project guides, audit reports, P1/B0 reports, actual source implementations,
installed smolagents lifecycle, current catalog/configs, and Day 8--13 artifacts. No Docker was
started or changed.

The following implementation facts were re-confirmed:

- smolagents appends a completed ActionStep to Memory and yields it before the next model call;
- current Day 8 reads succinct Memory only after `agent.run()` finishes;
- ExecutionMonitor records the Python tool result, not the exact wrapped Tool Response;
- v1 Checkpoint restores Environment + custom RuntimeState, not complete Agent messages;
- current branch runner is `deterministic_local_reference` and uses zero model Tokens;
- F0--F6 Injector is not connected to the real Agent path; F6 lacks `unknown` and leaks commit
  truth in exception text;
- current verifier is deterministic but needs an outer outcome/infra classifier and new-template
  probes.

## Remote Environment Work

None. No SSH, HTTPS API, vLLM, remote service, tunnel, GPU query, GPU allocation, model download,
or model call was performed. Resource impact: 0 GPU-hours and 0 new model Tokens.

## Work Completed

1. Recommended the first eligible non-terminal Tool Observation as the one-per-task primary
   decision point; multiple Checkpoints are a separate clustered extension.
2. Froze `Y_continue` as 1/0/null and defined max-budget, partial success, lost response,
   infrastructure failure and manual-abort boundaries.
3. Defined complete Agent + Runtime + Environment Checkpoint v2 and an exact first-model-input
   byte/hash acceptance test.
4. Defined component/combined hash membership and volatile-field normalization.
5. Created a 21-feature candidate registry with mathematical definitions and provenance fields.
   All remain disabled pending B2 evidence; internal/future fields are permanently forbidden.
6. Froze F0--F6 visible behavior and safety rules, including F6 three-state handling and true F5
   trace evidence.
7. Preserved all 40 existing template IDs/splits and froze the 40-template 6/2/2-per-domain
   addition contract without inventing unvalidated templates.
8. Defined four append-only record types, primary/foreign keys, lineage, typed missingness and
   checksums.
9. Defined grouped evaluation, cluster bootstrap, single-class handling, one-time Test access,
   model gates and the limits of 72/24/24.
10. Estimated model requests, Tokens, tool calls, GPU time, disk and elapsed time.
11. Defined hard B2, B3 and B4 implementation/smoke Gates.

## Research Protocol Summary

### Decision and label

- Primary: first eligible non-terminal completed ActionStep Observation batch after Memory append
  and before the next inference.
- Source attempt stops as `checkpoint_captured`; only independent CONTINUE produces the label.
- Verifier pass=1; valid verifier fail=0; unobservable/infra-censored outcome=null.
- Healthy max-step/token/tool exhaustion with failed verifier=0; vLLM/TLS/environment outage that
  prevents the result=null.

### Faults and features

- Tool errors are inputs only when the exact response was received by Agent; injector F-code is
  audit-only.
- F6 `committed`/`unknown` cannot blind-retry.
- F5 must be real trace behavior/no progress, not a controller flag.
- No candidate feature is enabled before B2 provenance; current historical synthetic features are
  not grandfathered in.

### Scale and split

- Target 80 templates, 48/16/16; target 160 task pairs and Checkpoints, 96/32/32.
- Minimum valid Checkpoints 72/24/24 (120 total).
- Existing 40 remain single-step compatibility inputs; 40 new 3--8-step template definitions and
  catalog hash are pending.
- Template-level split controls every instance, Checkpoint, repeat and branch.

## Engineering Protocol Summary

- Checkpoint schema v2 requires exact Messages/Memory, Tool Calls/Responses, Prompts, Agent state,
  tools/config, Workspace, SQLite, Mock API, side effects, budgets and adapter state.
- Restore must reproduce canonical next-model-input bytes/hash before the first request.
- Combined hash is built from Agent/input, Environment, Runtime and identity-config hashes;
  timestamps, absolute paths, inode/mtime and logging-only UUIDs are normalized out unless they
  affect messages/future behavior.
- B2 requires 5/5 complete real Checkpoints and exact Restore/input; B3 requires 5/5 full Qwen
  CONTINUE -> Verifier chains; B4 requires 8--12 non-formal tasks and at least 5 full chains.

## Data Quality and Statistical Scope

- Constant prevalence and regularized Logistic are mandatory.
- LightGBM requires at least 200 Train rows and 30/class, so it is not expected to qualify at the
  minimum Gate.
- CV, split and bootstrap are template-clustered; repeated Checkpoints also retain task clusters.
- Single-class AUROC/AUPRC is undefined.
- 72/24/24 supports pipeline feasibility and only coarse, large-effect independent evidence. It
  does not guarantee significance, calibration stability, subgroup conclusions or narrow CIs.

## Resource Usage and Budget

- B1 actual: 0 model calls, 0 Tokens, 0 GPU, 0 remote/Docker changes.
- Formal planning: about 480--880 Qwen requests (about 530--970 with safe infra reserve),
  2.4--6.4M expected Tokens, 10.5M conservative ceiling, 400--1,200 expected tool calls.
- At most two approved free RTX 4090s; 6--12 service wall-hours, roughly 12--24 GPU-card-hours.
- Reserve 5--10 GB local disk; require at least 20 GB free at preflight.
- Shared resource scheduling may spread collection across 3--5 calendar days.

## Files Changed

Added only:

- `docs/phase2/protocol_risk_v2.md`
- `docs/phase2/schema_risk_v2.md`
- `docs/phase2/feature_allowlist.json`
- `docs/phase2/checkpoint_schema_v2.json`
- `docs/phase2/failure_taxonomy_v2.md`
- `configs/phase2_risk_collection.yaml`
- `docs/phase2/task_split_manifest_v2.md`
- `docs/phase2/collection_budget_v2.md`
- `docs/phase2/b2_b4_acceptance_protocol.md`
- `docs/phase2/daily_reports/b1_protocol_report.md`

No source code, test code, historical run/model/report, external Recovery-Bench file, Agent
Factory, CheckpointManager, Branch Runner, catalog or remote config was modified.

## Artifacts Generated

The ten files above are the complete B1 artifact set. No run directory, model, Checkpoint or
formal dataset was generated.

## Commands and Tests

Representative audit/validation commands:

```bash
pwd
git status --short
git log -5 --oneline
.venv/bin/python --version
.venv/bin/python -c 'import smolagents, openai; ...'
rg/sed <required documents, source, configs, installed smolagents lifecycle>
.venv/bin/python scripts/audit_phase2_history.py --check-only
.venv/bin/python scripts/audit_p1_d2_risk_features.py --check-only
.venv/bin/python scripts/audit_p1_d3_utility_policy.py --check-only
.venv/bin/python -m json.tool docs/phase2/feature_allowlist.json
.venv/bin/python -m json.tool docs/phase2/checkpoint_schema_v2.json
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
git diff --check
```

A read-only structured audit also reloaded `configs/task_templates.yaml`, regenerated the current
catalog in memory, checked split/domain/step counts, and read Day 8/9/13 JSONL counts. It wrote no
historical artifact.

## Test Results

- B1 JSON/YAML assertions: PASS; both JSON files parse, 21 unique candidate features contain all
  required provenance fields, all are disabled pending evidence, config targets are 160 and
  72/24/24 minimum.
- `json.tool`: PASS for both JSON artifacts.
- Draft 2020-12 meta-Schema validation: not run because the project environment does not install
  the optional `jsonschema` package (`ModuleNotFoundError`); no dependency was added for B1.
- `pip check`: `No broken requirements found.`
- Full regression: `84 passed in 8.56s`.
- `git diff --check`: PASS.
- P1-D1 audit: 11/11 checks true.
- P1-D2 audit: 12/12 checks true.
- P1-D3 audit: all listed checks true and outputs match recomputation.
- No remote, Docker or model-backed test was run; B1 must not claim B2/B3 evidence.

## Acceptance Checklist

| Item | B1 status | Evidence/reason |
|---|---|---|
| Risk label | PASS | 1/0/null and edge cases are unambiguous |
| Decision moment | PASS | Current smolagents lifecycle and B0 spy probe support the boundary |
| Complete Checkpoint | CONDITIONAL PASS | Schema/Gate complete; B2 implementation/Restore proof absent |
| Feature allowlist | CONDITIONAL PASS | 21 sources/rules specified; all remain pending B2 provenance |
| Fault taxonomy | CONDITIONAL PASS | Semantics/safety complete; real Agent response adapter and F6 unknown absent |
| Task catalog | BLOCKED for formal collection | Current 40 are single-step; new 40 not implemented/validated |
| Data split | CONDITIONAL PASS | Existing IDs isolated; new IDs/hash pending |
| Data schema | PASS | Four records, lineage, missingness and checksums defined |
| Evaluation | PASS | Models, metrics, grouping, bootstrap and Test rules defined |
| Resource budget | PASS as estimate | Based on audited Day 8 plus conservative multi-step range; update after smoke |
| B2 Gate | PASS as specification | Exact tests and hard failure conditions defined |
| B3 Gate | PASS as specification | Five model-backed chains and label rules defined |
| B4 Gate | PASS as specification | 8--12 tasks, >=5 chains, non-formal isolation and cleanup defined |

No item requiring B2/B3 implementation is marked implemented PASS.

## Known Issues / Human Decisions Required

1. User approval is required to promote `risk-continue-v2.0.0-rc1` from review candidate to frozen.
2. Approve or revise the primary first-Observation rule and source-run stop-after-capture rule.
3. Approve or revise the limits: 10 model steps/calls, 12 tool calls, 50k cumulative Tokens,
   8,192 context and 900-second task wall time.
4. Approve the policy that all 21 candidate features stay disabled until B2 evidence, including
   monitor-side budget/latency features not literally visible in Agent Memory.
5. Approve the F4/F6 coverage gaps: F4 required for stateful API; F6 currently API-write only.
6. Approve the LightGBM sample Gate (>=200 Train rows and >=30/class), which likely makes it
   exploratory/unavailable for the planned 96-Train target.
7. Exact new 40 template IDs, definitions, fault schedule and catalog hash require a later
   reviewed artifact; B1 intentionally did not fabricate them.
8. Any future real Qwen/GPU test requires separate resource confirmation at execution time.

## Remaining Work

- After user approval, B2 may implement schema v2 and lifecycle capture using existing
  Train/development tasks.
- Formal collection remains blocked until B2, B3, B4 and the v2 catalog/fault manifest all pass.
- B1 does not authorize B2 code changes, remote service actions or data collection.

## Status: CONDITIONAL PASS

B1 protocol design is complete and internally validated. The condition is explicit user approval
of rc1 plus the pending catalog/implementation evidence. It is reasonable to enter B2 **only after
approval**, while keeping formal collection blocked.

## Git Commit

No commit created. Existing untracked user files were preserved, and B1 additions remain
untracked for review.
