"""Deterministic fault injection and action-safety checks for Day 4."""

from .config import load_fault_configs
from .injector import FaultInjector, InjectedFaultError, PartialFailureError
from .operations import describe_operation
from .safety import RetryGuard, RetrySafetyEvaluator, UnsafeRetryError
from .schema import (
    Action,
    CommitStatus,
    FaultConfig,
    FaultEvent,
    FaultType,
    ToolOperation,
)

__all__ = [
    "Action",
    "CommitStatus",
    "FaultConfig",
    "FaultEvent",
    "FaultInjector",
    "FaultType",
    "InjectedFaultError",
    "PartialFailureError",
    "RetryGuard",
    "RetrySafetyEvaluator",
    "ToolOperation",
    "UnsafeRetryError",
    "load_fault_configs",
    "describe_operation",
]
