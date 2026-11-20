"""Task-level snapshot and restore without modifying Recovery-Bench."""

from .manager import (
    CheckpointIntegrityError,
    CheckpointManager,
    CheckpointStateMismatchError,
    RestoreResult,
    SnapshotResult,
    normalized_state_hash,
)
from .metadata import (
    AdapterStateMetadata,
    AdapterStateStatus,
    CaptureInvariants,
    CheckpointIdentityV2,
    EnvironmentCheckpointMetadata,
    RuntimeCheckpointMetadata,
)
from .schema import (
    ArtifactRecordV2,
    CheckpointManifest,
    CheckpointManifestV2,
    CheckpointSchemaError,
    parse_checkpoint_manifest,
)
from .v2_manager import RestoreV2Result, SnapshotV2Result, VerifyV2Result

__all__ = [
    "AdapterStateMetadata",
    "AdapterStateStatus",
    "ArtifactRecordV2",
    "CaptureInvariants",
    "CheckpointIntegrityError",
    "CheckpointIdentityV2",
    "CheckpointManager",
    "CheckpointManifest",
    "CheckpointManifestV2",
    "CheckpointSchemaError",
    "CheckpointStateMismatchError",
    "EnvironmentCheckpointMetadata",
    "RestoreResult",
    "RestoreV2Result",
    "RuntimeCheckpointMetadata",
    "SnapshotResult",
    "SnapshotV2Result",
    "VerifyV2Result",
    "normalized_state_hash",
    "parse_checkpoint_manifest",
]
