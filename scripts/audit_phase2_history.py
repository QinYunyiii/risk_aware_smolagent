#!/usr/bin/env python3
"""Build the immutable Day 7--14 baseline used by the Phase 2 audits.

The historical directories are read-only inputs.  This script only writes the
requested Phase 2 manifest and refuses to replace it unless ``--force`` is
provided explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable


FORMAL_STAGES: dict[str, dict[str, Any]] = {
    "day07": {
        "run_id": "day7_20261118_033944_4b4bdfa9",
        "run_path": "runs/day07/day7_20261118_033944_4b4bdfa9",
        "configs": ["configs/task_templates.yaml", "configs/tasks.yaml"],
        "summary": "runs/day07/day7_20261118_033944_4b4bdfa9/task_results.json",
        "report": "docs/daily_reports/day_07_report.md",
    },
    "day08": {
        "run_id": "day8_20261119_074032_7ec4c41e",
        "run_path": "runs/day08/day8_20261119_074032_7ec4c41e",
        "configs": ["configs/day8_collection.yaml"],
        "summary": "runs/day08/day8_20261119_074032_7ec4c41e/collection_summary.json",
        "report": "docs/daily_reports/day_08_report.md",
    },
    "day09": {
        "run_id": "day9_20261119_080843_288841fc",
        "run_path": "runs/day09/day9_20261119_080843_288841fc",
        "configs": ["configs/day9_interventions.yaml"],
        "summary": "runs/day09/day9_20261119_080843_288841fc/intervention_summary.json",
        "report": "docs/daily_reports/day_09_report.md",
    },
    "day10": {
        "run_id": "day10_20261119_082058_aab1d057",
        "run_path": "runs/day10/day10_20261119_082058_aab1d057",
        "model_path": "models/risk/day10_20261119_082058_aab1d057",
        "configs": ["configs/day10_risk.yaml"],
        "summary": "runs/day10/day10_20261119_082058_aab1d057/training_summary.json",
        "report": "docs/daily_reports/day_10_report.md",
    },
    "day11": {
        "run_id": "day11_20261119_082900_e8d013ce",
        "run_path": "runs/day11/day11_20261119_082900_e8d013ce",
        "model_path": "models/utility/day11_20261119_082900_e8d013ce",
        "configs": ["configs/day11_utility.yaml"],
        "summary": "runs/day11/day11_20261119_082900_e8d013ce/training_summary.json",
        "report": "docs/daily_reports/day_11_report.md",
    },
    "day12": {
        "run_id": "day12_20261119_083943_b18d954e",
        "run_path": "runs/day12/day12_20261119_083943_b18d954e",
        "configs": ["configs/day12_policy.yaml"],
        "summary": "runs/day12/day12_20261119_083943_b18d954e/policy_summary.json",
        "report": "docs/daily_reports/day_12_report.md",
    },
    "day13": {
        "run_id": "day13_20261119_085002_5754c690",
        "run_path": "runs/day13/day13_20261119_085002_5754c690",
        "configs": ["configs/day13_experiment.yaml"],
        "summary": "runs/day13/day13_20261119_085002_5754c690/main_experiment_summary.json",
        "report": "docs/daily_reports/day_13_report.md",
    },
    "day14": {
        "run_id": "day14_20261119_090131_2c077c82",
        "run_path": "runs/day14/day14_20261119_090131_2c077c82",
        "configs": ["configs/day14_recovery_bench.yaml"],
        "summary": "runs/day14/day14_20261119_090131_2c077c82/recovery_bench_demo_summary.json",
        "report": "docs/daily_reports/day_14_report.md",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _file_record(root: Path, relative_path: str, role: str) -> dict[str, Any]:
    path = root / relative_path
    return {
        "path": relative_path,
        "role": role,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _tree_record(root: Path, relative_path: str, role: str) -> dict[str, Any]:
    directory = root / relative_path
    digest = hashlib.sha256()
    count = 0
    size_bytes = 0
    for path in sorted(candidate for candidate in directory.rglob("*") if candidate.is_file()):
        relative_file = path.relative_to(directory).as_posix()
        file_size = path.stat().st_size
        file_hash = _sha256(path)
        digest.update(f"{relative_file}\0{file_size}\0{file_hash}\n".encode("utf-8"))
        count += 1
        size_bytes += file_size
    return {
        "path": relative_path,
        "role": role,
        "file_count": count,
        "size_bytes": size_bytes,
        "tree_sha256": digest.hexdigest(),
        "tree_hash_encoding": "sorted(relative_path + NUL + size_bytes + NUL + file_sha256 + LF)",
    }


def _duplicates(rows: Iterable[dict[str, Any]], key: Callable[[dict[str, Any]], Any]) -> int:
    counts = Counter(key(row) for row in rows)
    return sum(1 for count in counts.values() if count > 1)


def _missing(rows: Iterable[dict[str, Any]], key: str) -> int:
    return sum(row.get(key) in (None, "") for row in rows)


def _analyse(root: Path) -> dict[str, Any]:
    day07 = root / FORMAL_STAGES["day07"]["run_path"]
    day08 = root / FORMAL_STAGES["day08"]["run_path"]
    day09 = root / FORMAL_STAGES["day09"]["run_path"]
    day10 = root / FORMAL_STAGES["day10"]["run_path"]
    day11 = root / FORMAL_STAGES["day11"]["run_path"]
    day12 = root / FORMAL_STAGES["day12"]["run_path"]
    day13 = root / FORMAL_STAGES["day13"]["run_path"]
    day14 = root / FORMAL_STAGES["day14"]["run_path"]

    day07_tasks = _json(day07 / "task_catalog.json")["tasks"]
    day08_attempts = _jsonl(day08 / "task_trajectories.jsonl")
    day08_logical = _json(day08 / "logical_task_results.json")["records"]
    day09_checkpoints = _json(day09 / "checkpoint_manifest.json")["checkpoints"]
    day09_branches = _jsonl(day09 / "branch_outcomes.jsonl")
    day10_examples = _json(day10 / "risk_dataset.json")["examples"]
    day11_examples = _json(day11 / "utility_training_dataset.json")["examples"]
    day12_records = _json(day12 / "policy_e2e_records.json")["records"]
    day13_policy = _jsonl(day13 / "policy_task_results.jsonl")
    day13_branches = _jsonl(day13 / "selected_branch_outcomes.jsonl")
    day14_trials = _jsonl(day14 / "selected_trials.jsonl")

    risk_metadata = _json(root / FORMAL_STAGES["day10"]["model_path"] / "model_metadata.json")
    utility_metadata = _json(root / FORMAL_STAGES["day11"]["model_path"] / "model_metadata.json")
    day09_run_path = FORMAL_STAGES["day09"]["run_path"]

    policy_execution_by_branch = {
        record["execution"]["branch_id"]: record["execution"] for record in day13_policy
    }
    branch_by_id = {record["branch_id"]: record for record in day13_branches}
    shared_branch_ids = policy_execution_by_branch.keys() & branch_by_id.keys()

    analysis = {
        "day07": {
            "task_count": len(day07_tasks),
            "unique_task_id_count": len({row["task_id"] for row in day07_tasks}),
            "split_counts": dict(sorted(Counter(row["split"] for row in day07_tasks).items())),
            "missing_split_count": _missing(day07_tasks, "split"),
        },
        "day08": {
            "logical_task_count": len(day08_logical),
            "raw_attempt_count": len(day08_attempts),
            "unique_logical_task_id_count": len({row["task_id"] for row in day08_logical}),
            "unique_task_run_id_count": len({row["task_run_id"] for row in day08_attempts}),
            "logical_duplicate_task_id_groups": _duplicates(day08_logical, lambda row: row["task_id"]),
            "attempt_duplicate_task_run_id_groups": _duplicates(day08_attempts, lambda row: row["task_run_id"]),
            "repeated_logical_task_id_groups_in_attempts": _duplicates(day08_attempts, lambda row: row["task_id"]),
            "attempts_per_task_histogram": {
                str(key): value
                for key, value in sorted(Counter(Counter(row["task_id"] for row in day08_attempts).values()).items())
            },
            "status_counts": dict(sorted(Counter(row["task_status"] for row in day08_logical).items())),
            "missing_split_count": _missing(day08_attempts, "split"),
            "missing_controller_type_count": _missing(day08_attempts, "controller_type"),
        },
        "day09": {
            "checkpoint_count": len(day09_checkpoints),
            "unique_checkpoint_id_count": len({row["checkpoint_id"] for row in day09_checkpoints}),
            "branch_record_count": len(day09_branches),
            "unique_branch_id_count": len({row["branch_id"] for row in day09_branches}),
            "unique_checkpoint_action_pair_count": len(
                {(row["checkpoint_id"], row["action"]) for row in day09_branches}
            ),
            "applicable_branch_count": sum(bool(row["action_applicable"]) for row in day09_branches),
            "action_applicability_counts": {
                f"{action}:{str(applicable).lower()}": count
                for (action, applicable), count in sorted(
                    Counter((row["action"], row["action_applicable"]) for row in day09_branches).items()
                )
            },
            "missing_checkpoint_hash_count": _missing(day09_checkpoints, "checkpoint_hash"),
            "missing_state_hash_count": _missing(day09_checkpoints, "state_hash"),
            "missing_split_checkpoint_count": _missing(day09_checkpoints, "split"),
            "missing_split_branch_count": _missing(day09_branches, "split"),
            "missing_controller_type_count": _missing(day09_branches, "controller_type"),
            "controller_type_counts": dict(
                sorted(Counter(row.get("controller_type") for row in day09_branches).items())
            ),
            "unknown_checkpoint_reference_count": len(
                {row["checkpoint_id"] for row in day09_branches}
                - {row["checkpoint_id"] for row in day09_checkpoints}
            ),
        },
        "day10": {
            "example_count": len(day10_examples),
            "missing_split_count": _missing(day10_examples, "split"),
            "metadata_source_day9_run": risk_metadata.get("source_day9_run"),
            "feature_count": len(risk_metadata.get("feature_names", [])),
            "unique_feature_count": len(set(risk_metadata.get("feature_names", []))),
        },
        "day11": {
            "example_count": len(day11_examples),
            "applicable_example_count": sum(bool(row["action_applicable"]) for row in day11_examples),
            "missing_split_count": _missing(day11_examples, "split"),
            "metadata_source_day9_run": utility_metadata.get("source_day9_run"),
            "feature_count": len(utility_metadata.get("feature_names", [])),
            "unique_feature_count": len(set(utility_metadata.get("feature_names", []))),
            "feature_schema_matches_day10": utility_metadata.get("feature_names")
            == risk_metadata.get("feature_names"),
        },
        "day12": {
            "policy_record_count": len(day12_records),
            "missing_decision_split_count": sum(
                row["decision"].get("split") in (None, "") for row in day12_records
            ),
            "missing_execution_split_count": sum(
                row["execution"].get("split") in (None, "") for row in day12_records
            ),
            "missing_execution_controller_type_count": sum(
                row["execution"].get("controller_type") in (None, "") for row in day12_records
            ),
        },
        "day13": {
            "policy_record_count": len(day13_policy),
            "branch_result_count": len(day13_branches),
            "unique_policy_checkpoint_pair_count": len(
                {(row["policy"], row["checkpoint_id"]) for row in day13_policy}
            ),
            "unique_policy_execution_branch_id_count": len(policy_execution_by_branch),
            "unique_branch_result_id_count": len(branch_by_id),
            "policy_only_branch_id_count": len(policy_execution_by_branch.keys() - branch_by_id.keys()),
            "result_only_branch_id_count": len(branch_by_id.keys() - policy_execution_by_branch.keys()),
            "execution_payload_mismatch_count": sum(
                policy_execution_by_branch[key] != branch_by_id[key] for key in shared_branch_ids
            ),
            "missing_split_count": _missing(day13_policy, "split"),
            "missing_execution_controller_type_count": sum(
                row["execution"].get("controller_type") in (None, "") for row in day13_policy
            ),
            "policy_counts": dict(sorted(Counter(row["policy"] for row in day13_policy).items())),
        },
        "day14": {
            "selected_trial_count": len(day14_trials),
            "branch_utility_eligible_count": sum(
                bool(row["branch_utility_eligible"]) for row in day14_trials
            ),
        },
    }

    analysis["gate_checks"] = {
        "day07_200_unique_tasks": analysis["day07"]["task_count"]
        == analysis["day07"]["unique_task_id_count"]
        == 200,
        "day08_24_logical_43_attempts": analysis["day08"]["logical_task_count"] == 24
        and analysis["day08"]["raw_attempt_count"] == 43,
        "day08_attempt_and_logical_ids_unique": analysis["day08"]["unique_task_run_id_count"] == 43
        and analysis["day08"]["unique_logical_task_id_count"] == 24,
        "day09_20_checkpoints_60_actions_36_applicable": analysis["day09"]["checkpoint_count"] == 20
        and analysis["day09"]["branch_record_count"] == 60
        and analysis["day09"]["applicable_branch_count"] == 36,
        "day09_identifiers_unique_and_referentially_complete": analysis["day09"]["unique_checkpoint_id_count"] == 20
        and analysis["day09"]["unique_branch_id_count"] == 60
        and analysis["day09"]["unknown_checkpoint_reference_count"] == 0,
        "day09_state_hash_available_for_all_checkpoints": analysis["day09"]["missing_state_hash_count"] == 0,
        "day10_day11_source_day09": analysis["day10"]["metadata_source_day9_run"] == day09_run_path
        and analysis["day11"]["metadata_source_day9_run"] == day09_run_path,
        "day10_day11_feature_schema_26_unique_and_equal": analysis["day10"]["feature_count"] == 26
        and analysis["day10"]["unique_feature_count"] == 26
        and analysis["day11"]["feature_count"] == 26
        and analysis["day11"]["unique_feature_count"] == 26
        and analysis["day11"]["feature_schema_matches_day10"],
        "day13_180_records_one_to_one": analysis["day13"]["policy_record_count"] == 180
        and analysis["day13"]["branch_result_count"] == 180
        and analysis["day13"]["unique_policy_checkpoint_pair_count"] == 180
        and analysis["day13"]["policy_only_branch_id_count"] == 0
        and analysis["day13"]["result_only_branch_id_count"] == 0
        and analysis["day13"]["execution_payload_mismatch_count"] == 0,
        "applicable_split_fields_complete": all(
            count == 0
            for count in (
                analysis["day07"]["missing_split_count"],
                analysis["day08"]["missing_split_count"],
                analysis["day09"]["missing_split_checkpoint_count"],
                analysis["day09"]["missing_split_branch_count"],
                analysis["day10"]["missing_split_count"],
                analysis["day11"]["missing_split_count"],
                analysis["day12"]["missing_decision_split_count"],
                analysis["day12"]["missing_execution_split_count"],
                analysis["day13"]["missing_split_count"],
            )
        ),
        "branch_controller_type_fields_complete": all(
            count == 0
            for count in (
                analysis["day09"]["missing_controller_type_count"],
                analysis["day12"]["missing_execution_controller_type_count"],
                analysis["day13"]["missing_execution_controller_type_count"],
            )
        ),
    }
    return analysis


def build_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    required_paths: set[str] = {
        "AGENTS.md",
        "PROJECT_GUIDE.md",
        "README.md",
        "NEXT_STAGE_EXPERIMENT_GUIDE.md",
    }
    tree_specs: list[tuple[str, str]] = []
    stage_inventory: dict[str, Any] = {}
    for stage, spec in FORMAL_STAGES.items():
        inventory = dict(spec)
        stage_inventory[stage] = inventory
        required_paths.update(spec["configs"])
        required_paths.add(spec["summary"])
        required_paths.add(spec["report"])
        tree_specs.append((spec["run_path"], f"{stage} formal run"))
        if model_path := spec.get("model_path"):
            tree_specs.append((model_path, f"{stage} final models"))

    missing = sorted(path for path in required_paths if not (root / path).is_file())
    missing.extend(sorted(path for path, _ in tree_specs if not (root / path).is_dir()))
    if missing:
        raise FileNotFoundError("Missing required historical paths: " + ", ".join(missing))

    key_files: dict[str, str] = {}
    for path in required_paths:
        if path.endswith("_report.md"):
            key_files[path] = "historical daily report"
        elif path.startswith("configs/"):
            key_files[path] = "historical configuration"
        elif path.endswith(".md"):
            key_files[path] = "governance or experiment guide"
        else:
            key_files[path] = "formal summary"
    for path, role in tree_specs:
        for child in sorted((root / path).iterdir()):
            if child.is_file():
                key_files[child.relative_to(root).as_posix()] = f"{role} key artifact"

    trees = [_tree_record(root, path, role) for path, role in tree_specs]
    analysis = _analyse(root)
    return {
        "schema_version": "phase2-historical-artifact-manifest-v1",
        "scope": "P1-D1 immutable Day 7--14 historical baseline",
        "project_root": ".",
        "hash_algorithm": "SHA-256",
        "stage_inventory": stage_inventory,
        "key_artifacts": [
            _file_record(root, path, key_files[path]) for path in sorted(key_files)
        ],
        "historical_trees": trees,
        "audit": analysis,
        "recorded_findings": [
            {
                "code": "DAY08_CONTROLLER_TYPE_ABSENT",
                "severity": "schema_observation",
                "count": analysis["day08"]["missing_controller_type_count"],
                "detail": "Day 8 model-attempt records predate the branch controller schema and do not contain controller_type.",
            },
            {
                "code": "DAY09_CHECKPOINT_HASH_ALIAS",
                "severity": "schema_observation",
                "count": analysis["day09"]["missing_checkpoint_hash_count"],
                "detail": "Day 9 checkpoint records use state_hash for all checkpoints; no separate checkpoint_hash field exists.",
            },
            {
                "code": "DAY08_EXPECTED_TASK_RETRIES",
                "severity": "informational",
                "count": analysis["day08"]["repeated_logical_task_id_groups_in_attempts"],
                "detail": "Repeated task_id values are expected retries; task_run_id remains unique.",
            },
        ],
        "gate_passed": all(analysis["gate_checks"].values()),
        "research_claim": "None. This manifest establishes provenance and integrity only.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/phase2/historical_artifact_manifest.json"),
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    manifest = build_manifest(root)
    if not args.check_only:
        output = args.output if args.output.is_absolute() else root / args.output
        if output.exists() and not args.force:
            raise FileExistsError(f"Refusing to replace existing manifest: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(output)
    print(json.dumps({"gate_passed": manifest["gate_passed"], "checks": manifest["audit"]["gate_checks"]}, indent=2))
    return 0 if manifest["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
