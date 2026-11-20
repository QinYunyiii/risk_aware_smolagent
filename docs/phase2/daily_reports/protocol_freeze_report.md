# Workstream B - Protocol v2 Freeze Report

## Objectives

Promote the reviewed `risk-continue-v2.0.0-rc1` contract to the formal
`risk-continue-v2.0.0` version without changing research meaning, and establish immutable source
hashes, approval scope and change control before B2 implementation.

## Baseline

- Source commit: `002df218adf10c7a792c9b4e3cb4c1d9a62b8c8d`
- Local branch: `master`; tracked remote branch: `origin/main`
- Remote source ref was verified at the same commit.
- Working tree was clean before freeze edits.
- Historical B1 report remained unchanged.

## Work Completed

- Compared every requested default decision with rc1; found no semantic conflict.
- Recorded SHA-256 for nine rc1 protocol/config artifacts before editing.
- Promoted Protocol, data schema, Checkpoint schema, taxonomy, feature registry, acceptance
  protocol and collection config markers from rc1/review candidate to formal/frozen.
- Preserved every `PENDING_B2` marker and the partial v2 catalog status.
- Added `protocol_freeze_record_v2.md` with approved, unfrozen and unapproved scope.
- Defined versioned change control; no B2 difficulty may silently change the estimand or label.

## Files Changed

- `docs/phase2/protocol_freeze_record_v2.md` (new)
- `docs/phase2/daily_reports/protocol_freeze_report.md` (new)
- `docs/phase2/protocol_risk_v2.md`
- `docs/phase2/schema_risk_v2.md`
- `docs/phase2/feature_allowlist.json`
- `docs/phase2/checkpoint_schema_v2.json`
- `docs/phase2/failure_taxonomy_v2.md`
- `docs/phase2/b2_b4_acceptance_protocol.md`
- `configs/phase2_risk_collection.yaml`

## Explicitly Unchanged

- B1 historical daily report;
- task IDs/splits and current catalog;
- all source and test code;
- Day 1--14 and Phase 2 audit data;
- remote service/GPU state;
- risk labels, fault semantics, thresholds and local/remote deployment boundary.

## Validation

Validation commands and results are recorded in the freeze commit and final handoff. At minimum:

- JSON parsing and version-marker assertions;
- YAML parsing and frozen Protocol ID assertion;
- RC1 source-hash table verification against pre-edit output;
- repository regression tests;
- Git diff/status review.

## Approval and Remaining Decisions

The current user task explicitly adopts the listed recommended freeze decisions. It does not
approve the concrete 40-template expansion, feature activation, real GPU use, B3/B4, formal data
collection or model training. Those remain blocked behind their own approvals/Gates.

## Status: PASS

The formal research/data contract is frozen. PASS here does not assert B2 implementation or any
real Qwen Restore capability.

## Git Commit

This report and the freeze record are committed together in a dedicated Phase 0 commit. Resolve
the non-self-referential commit SHA with:

```bash
git log -1 --oneline -- docs/phase2/protocol_freeze_record_v2.md
```
