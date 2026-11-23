"""Real-model trajectory collection with local benchmark tools."""

from .benchmark_tools import (
    CompletedToolReplayError,
    LocalBenchmarkTool,
    canonical_tool_call_signature,
    capture_local_tool_adapter_state,
    create_local_domain_tools,
    restore_local_tool_adapter_state,
)
from .b4_smoke_gate import (
    B4_RESEARCH_FLAGS,
    B4ConfigurationError,
    B4SmokeGateRunner,
    B4SmokePlan,
    B4TaskCase,
    b4_plan_sha256,
    b4_run_manifest,
    load_b4_smoke_plan,
    run_b4_local_preflight,
    validate_b4_plan,
)
from .continue_gate import (
    B3_RESEARCH_FLAGS,
    CheckpointAuditRecord,
    CheckpointRunAudit,
    ContinueGateError,
    ContinueGateRunner,
    audit_checkpoint_run,
)
from .continue_outcome import (
    ContinueLineage,
    ContinueOutcome,
    ContinueOutcomeEvaluator,
    ContinueTermination,
)
from .decision_checkpoint import (
    CaptureDecision,
    DecisionCheckpointCapture,
    DecisionCheckpointRejected,
    DecisionCheckpointResult,
)
from .day5_gate import (
    Day5GateRunner,
    RUN_RESEARCH_FLAGS,
    create_unique_run_directory,
    expected_request_from_checkpoint,
    select_day5_candidates,
)
from .initial_trajectory import (
    InitialTrajectoryRunner,
    classify_task_status,
    is_retryable_pretool_infra,
    log_completeness,
    select_initial_tasks,
)

__all__ = [
    "InitialTrajectoryRunner",
    "B4_RESEARCH_FLAGS",
    "B4ConfigurationError",
    "B4SmokeGateRunner",
    "B4SmokePlan",
    "B4TaskCase",
    "B3_RESEARCH_FLAGS",
    "CaptureDecision",
    "CheckpointAuditRecord",
    "CheckpointRunAudit",
    "CompletedToolReplayError",
    "ContinueGateError",
    "ContinueGateRunner",
    "ContinueLineage",
    "ContinueOutcome",
    "ContinueOutcomeEvaluator",
    "ContinueTermination",
    "DecisionCheckpointCapture",
    "DecisionCheckpointRejected",
    "DecisionCheckpointResult",
    "Day5GateRunner",
    "LocalBenchmarkTool",
    "RUN_RESEARCH_FLAGS",
    "audit_checkpoint_run",
    "b4_plan_sha256",
    "b4_run_manifest",
    "canonical_tool_call_signature",
    "classify_task_status",
    "capture_local_tool_adapter_state",
    "create_local_domain_tools",
    "create_unique_run_directory",
    "expected_request_from_checkpoint",
    "log_completeness",
    "is_retryable_pretool_infra",
    "load_b4_smoke_plan",
    "run_b4_local_preflight",
    "select_initial_tasks",
    "select_day5_candidates",
    "restore_local_tool_adapter_state",
    "validate_b4_plan",
]
