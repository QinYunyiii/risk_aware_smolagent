# B2 Day 4 Independent Restore and Branch Isolation Design

## Scope and boundaries

Day 4 proves that one complete Checkpoint v2 can be read twice to create two initially identical,
physically separate and mutually isolated local execution branches. It also closes a Model Request
budget undercount discovered at the provider boundary. This is a local engineering test using a
deterministic scripted model. It does not call Qwen, use a GPU, run Docker, change frozen Protocol
v2 semantics or produce research-eligible data.

The historical `BranchRunner` remains unchanged and is not treated as complete Agent Checkpoint
v2 evidence. Both branches are restored independently through `CheckpointManager.restore_v2()`
from the same immutable checkpoint directory.

## Model Request budget semantics

`attempted_calls` counts entries into `RecordingModel.generate()`. A request rejected by local
contract validation increments `rejected_calls`, but is never delegated. A valid intercepted
request is likewise stopped locally. `executed_calls` increments immediately before delegation to
the underlying provider, while `succeeded_calls` increments only after a response returns.

The budget usage is therefore:

```text
model_calls_used = prior_model_calls + executed_calls
```

Provider failures consume budget because the external request was already attempted. Local
rejections and deliberate interception do not. At restored usage 9 of 10, one failed provider
request moves usage to 10 and the next request is rejected before delegation.

## Restore topology

The source fixture creates a real `TaskEnvironment`, SQLite state, RuntimeState, non-terminal
smolagents Memory and `AgentStateSnapshot`, then calls `snapshot_v2()`. Restore A and Restore B
each read the checkpoint separately into distinct base directories. A fresh ToolCallingAgent is
built for each restored snapshot and passed through `AgentStateAdapter.restore()`; the exact next
model input check must pass for both.

The checkpoint writer preserves key insertion order in the authoritative Agent state JSON. This
is required because smolagents renders nested Tool Call arguments into model-visible text using
Python dictionary order. Sorting nested keys on disk changes those bytes even though a canonical
content hash is unchanged. Other checkpoint artifacts retain their existing deterministic sorted
JSON representation; no schema or hash definition changed.

## Initial equivalence and physical isolation

The test compares Agent State, next Model Input, Environment, Runtime and Combined State hashes
from the actual restore results. The combined hash must also equal the source Manifest.

Workspace and SQLite paths differ. `os.path.samefile()` is false and SQLite device/inode pairs
differ. Initial file bytes and SQLite logical dumps match, and `PRAGMA integrity_check` returns
`ok` for each database.

RuntimeState, BudgetState, Agent Memory, Tool History and Tool State instances are checked for
identity separation. The Agent snapshots have distinct payload, Memory Step, Tool Call, canonical
message and generation-configuration containers. The two rebuilt Agents also have distinct Memory
and State objects.

## Cross-branch mutation probes

Branch A modifies an existing file, creates a file, deletes a file and creates an A-only sentinel.
It updates an order status/version and inserts an idempotency key in one committed SQLite
transaction. It then mutates budgets, Tool State, Agent Memory, Tool History, rebuilt Agent State,
an ActionStep Observation, an internal message list and Tool Call arguments. Branch B is compared
with its complete pre-mutation file, database, Runtime and Agent baseline.

Branch B then creates its own sentinel, commits a different order/idempotency mutation and changes
its Runtime and Agent state. Branch A is compared with the post-A snapshot. This supplies
bidirectional state-based evidence rather than relying only on object IDs.

## Original checkpoint immutability

Before restore, all checkpoint files including `manifest.json` are inventoried by relative path,
size and SHA-256. After both branches are mutated, the entire inventory is recalculated and must
be exactly equal. The successful audit covers 30 files under `agent/`, `environment/`, `runtime/`,
`identity/` and the root Manifest.

## Evidence and acceptance

The executable assertions live in `tests/test_checkpoint_v2_isolation.py` and the Model Request
budget regressions live in `tests/agent/test_checkpointable_agent.py`. Local evidence is stored
under `runs/phase2_b2_day4/20261121T162757+0800/` with explicit non-formal, local-scripted,
GPU-free and dataset-ineligible markers.

Day 4 passes only if every requested targeted suite, the full repository suite, compileall and
diff validation pass and all isolation/immutability assertions hold. This gate does not claim the
B2 real-Qwen Complete Checkpoint Gate, B3 CONTINUE labels, B4 smoke or formal collection.
