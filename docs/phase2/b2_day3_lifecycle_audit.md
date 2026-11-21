# B2 Day 3 smolagents Lifecycle Audit

## Scope and dependency

This audit was executed locally against `smolagents==1.26.0` installed at
`.venv/lib/python3.12/site-packages/smolagents`. No site-package file was modified.

## Source evidence

| Lifecycle point | Installed source | Finding |
|---|---|---|
| `MultiStepAgent.run` | `smolagents/agents.py:436` | Always assigns the task and System Prompt; `reset=False` still appends a new `TaskStep`. |
| `_run_stream` | `agents.py:540` | Unconditionally starts with `self.step_number = 1`. |
| Action execution | `agents.py:562-610` | Creates the ActionStep, calls `_step_stream`, finalizes it, appends it to Memory, yields it, then increments the step number. |
| callback timing | `agents.py:620` | `_finalize_step` invokes callbacks before the caller appends the step to Memory. |
| next model input | `agents.py:1276` | `_step_stream` calls `write_memory_to_messages()` immediately before `model.generate()`. |
| Tool batch | `agents.py:1361` | All calls settle, outputs are sorted by call ID, then `tool_calls` and combined observations are installed on the ActionStep. |
| Memory projection | `agents.py:758` | System Prompt followed by every Memory step's `to_messages()` output. |
| max-step handling | `agents.py:625` | Reaching the limit triggers another model request via `provide_final_answer`; it is not a valid decision capture point. |

## Verified event order

```text
model.generate -> Assistant tool call -> local tool(s) -> sorted observations
-> _finalize_step/callback -> memory.steps.append(ActionStep)
-> public stream yield(ActionStep) -> step_number += 1 -> next model.generate
```

At the public yield, `step_number` is the completed current step. The next execution step is
`step_number + 1`; remaining steps are `max_steps - step_number`.

## Local probes

The Day 3 integration probe used the real local smolagents stream with a scripted Model and a
write tool. At capture, Memory ended in the yielded ActionStep, the canonical Messages ended in a
Tool Response, model calls remained one, tool calls remained one, and the file counter was one.
Calling `generator.close()` while suspended at the yield did not execute the increment following
the yield and caused no model/tool call or file mutation.

Advancing a separate original-control generator reached the next real `model.generate()` boundary.
An independent Checkpoint restore rebuilt a fresh Agent and reached the same boundary through the
version-pinned resume loop. Both inputs were 4,303 canonical UTF-8 bytes and had SHA-256
`9d20ac3cfc656d617e87441736ebbca58506e35d77a144c0fbe04e27c52bdfef`.

## Resume decision

Neither `run(reset=True)` nor `run(reset=False)` can implement exact continuation: the former
clears Memory and both paths append a TaskStep, while `_run_stream` resets the cursor. Day 3 uses a
local `CheckpointableToolCallingAgent` subclass that delegates individual planning/action/tool
steps to smolagents and owns only the pinned outer continuation loop. It asserts version 1.26.0,
starts at the stored completed step plus one, adds no TaskStep or prompt, and does not replay the
completed action. No global monkey patch is used.

## Planning, adapter and budget state

Planning cadence and `planning_interval`, Agent state, Prompt templates, Tool Registry and
generation settings are restored by `AgentStateAdapter`. Runtime/Tool/Fault adapter state remains
in Checkpoint v2 and is supplied to freshly rebuilt local tools. Model Call usage is restored into
the recording boundary before its first request. Aggregate Token, Tool Call and execution-latency
budgets remain authoritative.

The frozen documents do not fully specify whether human pause duration counts toward the
wall-clock budget. Day 3 does not invent a rule: it restores accumulated execution latency exactly
and records the pause-duration question for later protocol clarification.
