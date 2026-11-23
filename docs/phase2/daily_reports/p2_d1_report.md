# P2-D1 Report

## Objectives

- Preserve the 40-template v1 catalog without modification.
- Add 40 deterministic, genuinely dependent multi-step templates.
- Produce an 80-template / 160-instance v2 catalog with auditable F0-F6 coverage.
- Validate locally with the existing Reference Runner and Deterministic Verifier only.

## Local Environment Work

All implementation and validation ran in the repository `.venv` under WSL. The
builder uses temporary, task-isolated workspaces for reference execution, negative
verifier probes, and fault probes. It writes only a new append-only catalog run.

No SSH, HTTPS model API, Docker, Harbor, remote worker, vLLM, GPU, or Qwen process
was accessed or started.

## Remote Environment Work

None. P2-D1 is a local-only catalog engineering stage.

## Work Completed

1. Added `configs/task_templates_v2.yaml`, which extends the unchanged v1 source.
2. Added 40 templates: 10 per domain, with 6/2/2 new templates per domain across
   Train / Validation / Test.
3. Added backward step references (`$ref` and `$template`) and made the Reference
   Runner resolve actual prior tool outputs.
4. Added in-memory record filtering/aggregation and path-list search tools so later
   steps consume earlier structured outputs rather than re-reading hidden state.
5. Added deterministic F0-F6 plans, legal target-step checks, intervention metadata,
   F6 commit-state control, and retry-safety validation.
6. Added a local catalog builder that separates model-visible input, evaluator
   control data, internal fault metadata, agent-visible fault observations, and
   verifier labels.
7. Added positive reference checks, reset checks, incorrect-result rejection probes,
   and fault reachability probes.

## Template and Split Distribution

The final catalog contains 80 unique templates and 160 planned task instances
(two instances per template).

| Scope | Train | Validation | Test | Total |
| --- | ---: | ---: | ---: | ---: |
| Existing v1 templates | 24 | 8 | 8 | 40 |
| New P2-D1 templates | 24 | 8 | 8 | 40 |
| Final templates | 48 | 16 | 16 | 80 |
| Final task instances | 96 | 32 | 32 | 160 |

Each domain has 20 final templates. Each domain received 10 new templates split
6 Train / 2 Validation / 2 Test.

## Domain Task Design

- Data analysis: `read_csv -> filter_records(previous rows) -> aggregate_records(filtered rows)`.
- File retrieval: `list_files -> search_paths(previous paths) -> read_file(matched path)`.
- API workflow: `get_order -> update_order(observed fields, idempotency key) -> get_order(updated ID)`;
  stale-observation scenarios explicitly refresh state before the write.
- Calculation and logic: `calculator -> calculator(previous result) -> validate_result(derived result)`.

Every new instance has 3-4 reference steps. All normal progress steps consume prior
outputs. F5's intentionally repeated call is explicitly identified and the following
step consumes that call's output.

## F0-F6 Coverage

New-template counts are:

| Fault | Templates |
| --- | ---: |
| F0 no fault | 8 |
| F1 timeout | 6 |
| F2 service unavailable | 6 |
| F3 invalid argument | 6 |
| F4 stale observation | 5 |
| F5 repeated action | 6 |
| F6 partial failure | 3 |

Every split contains all seven fault classes. Domain-level coverage is honest rather
than synthetically filled: API workflow covers F0-F6; data analysis, file retrieval,
and calculation/logic cover F0-F5. Their only missing Domain × Fault cell is F6,
because `update_order` is the catalog's only state-changing operation and carries an
idempotency key. The matrix records these three missing cells and their reason.

The three F6 templates cover:

- `not_committed`: write is not executed; idempotent retry is allowed.
- `committed`: write effect and idempotency record exist; blind retry is blocked and
  state inspection is required.
- `unknown`: the deterministic probe commits but reports an unknown commit state;
  blind retry is blocked and state inspection is required.

Two instances per F6 template produced six successful F6 reachability and safety
probes.

## Files Changed

- Catalog/config: `configs/task_templates_v2.yaml`.
- Catalog execution: `src/risk_agent/benchmark/step_references.py`,
  `runner.py`, `task_generator.py`, `template_loader.py`, `p2_catalog.py`, and
  `benchmark/__init__.py`.
- Local tools: `data_analysis_tools.py`, `file_retrieval_tools.py`,
  `task_tool_registry.py`, and `collection/benchmark_tools.py`.
- Fault semantics: `faults/schema.py`, `injector.py`, `operations.py`, and `safety.py`.
- Entrypoint/tests: `scripts/build_p2_d1_catalog.py` and
  `tests/test_p2_d1_catalog.py`.
- Report: this file.

The existing `configs/task_templates.yaml` was not edited.

## Artifacts Generated

Authoritative run: `runs/phase2_catalog/p2_d1_20261123_004/`

- `task_catalog.json`
- `split_manifest.json`
- `fault_coverage_matrix.json`
- `reference_validation.json`
- `run_manifest.json`
- `artifact_manifest.json`

Runs `p2_d1_20261123_001` through `_003` are earlier append-only local acceptance
artifacts retained for provenance. `_004` is authoritative: it separates the prompt
from environment fixtures and evaluator controls, separates agent-visible fault
observations from internal fault metadata and verifier labels, and uses
commit-state-neutral public error messages.

## Commands and Tests

```bash
.venv/bin/python -m pytest -q tests/test_p2_d1_catalog.py
.venv/bin/python scripts/build_p2_d1_catalog.py --run-id p2_d1_20261123_004
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

## Test Results

- P2-D1 tests: 6 passed.
- Full suite: 253 passed in 28.78 seconds with warnings disabled.
- Reference Runner: 160/160 passed; 160/160 resets reproduced initial state.
- Deterministic Verifier negative probes: 160/160 deliberately incorrect results
  rejected.
- New-task fault probes: 80/80 reached their configured target or completed F0;
  80/80 reset checks passed.
- Compileall: passed.
- Compileall, YAML duplicate-key audit, artifact hash audit, and `git diff --check`:
  passed.

## Artifact Hashes

- Canonical v2 task catalog SHA-256:
  `c37f9bb3dee6c8493e8a174aa977595769caf6b7cff8e1601bb60ccdc5b9d64c`
- Preserved canonical v1 catalog SHA-256:
  `8a76a5c2b5d826832f2c604477c606d699a19a8ceee35b923cedc336b9e9d775`
- `task_catalog.json`:
  `0aed3890ebe6ac67f373c01394407b07fde432f9cb2c4ab674153539f6e5dab0`
- `split_manifest.json`:
  `451c818e5087103f1e5cc470e3bece4a8894cb449e8bdd277989873672af879d`
- `fault_coverage_matrix.json`:
  `0a0f1b79741d24331344623f2ee9abdb9a3eb65140ab43b827bd13dbd0da848f`
- `reference_validation.json`:
  `4a06928171394f5606b09a00d8b1469a9bf95ccde82240d623f3cd1e48c6dc01`

## Acceptance Checklist

- [x] Original 40 templates preserved.
- [x] 40 multi-step templates added.
- [x] 80 unique templates total.
- [x] 20 templates per domain.
- [x] Train / Validation / Test = 48 / 16 / 16.
- [x] Each domain's new templates = 6 / 2 / 2.
- [x] Every new task has real backward tool dependencies and 3-8 steps.
- [x] F0-F6 matrix records split/domain/target counts and missing-cell reasons.
- [x] F6 committed / not-committed / unknown are designed and reached.
- [x] Reference Runner and Deterministic Verifier checks pass.
- [x] Agent input excludes fault plans, expected checks, and reference steps.
- [x] Catalog and split hashes generated.
- [x] Historical configuration and runs were not overwritten.
- [x] No real Qwen task, remote GPU, worker, tunnel, or service was started.

## Known Issues

- Reference success proves deterministic environment and verifier correctness, not
  that Qwen can solve the templates.
- F6 is intentionally absent outside API workflow because the other three domains
  have no meaningful committed write; adding fake F6 cases would weaken validity.
- Injected failures that halt before a terminal result carry an explicit
  `not_evaluated` verifier label rather than a fabricated success/failure label.
- Real-model prompt/tool-schema acceptance and empirical completion rates remain
  untested by design at P2-D1.

## Remaining Work

P2-D2 or any real-model collection must wait for explicit user approval. Before
formal collection, perform the next stage's checkpoint/branch protocol work and a
separately approved real-Qwen smoke gate without reusing B4 smoke data as training
data.

## Status: PASS

All P2-D1 core gates passed. Work stops here; P2-D2 was not started.

## Git Commit

Planned commit message: `feat: add P2-D1 multi-step task catalog`. The final commit
SHA and push result are reported in the handoff after repository verification.
