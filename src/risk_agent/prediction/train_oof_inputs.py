"""Join qualified Train labels to approved v2 sidecars; no mixed snapshot."""
from __future__ import annotations

from pathlib import Path

from .train_only_access import strict_path, sha256
from .train_evidence import canonical, load_train
from .train_day1_audit import enroll_train


def check_config(config, approval):
    expected_model = {"penalty": "l2", "C": .1, "solver": "lbfgs", "max_iter": 2000,
        "class_weight": None, "random_seed": 20261128}
    expected_fold = {"class": "StratifiedGroupKFold", "n_splits": 3, "group": "template_id",
                     "shuffle": True, "random_seed": 20261128}
    expected_bootstrap = {"unit": "template_id", "resamples": 1000, "random_seed": 20261128,
                          "delta": "v2_minus_v1", "refit": False}
    if (config["scope"] != "approved_paired_train_oof_only" or config["arms"] != ["v1", "v2"]
            or config["logistic"] != expected_model or config["folds"] != expected_fold
            or config["bootstrap"] != expected_bootstrap or config["threshold"] != .5
            or config["calibration_bins"] != 5 or config["calibration_strategy"] != "uniform"
            or config["numerical_threads"] != 1
            or config["preprocessing"] != {"base": "frozen_FormalPreprocessor",
                "new_bit": "train_fold_drop_constant_else_mean_std_scale", "unknown": "reject"}
            or any(config[k] is not False for k in ("full_train_final_fit", "validation_access", "test_access",
                "hyperparameter_search", "threshold_search", "lightgbm_enabled"))):
        raise ValueError("Only the fixed two-arm Train OOF comparison is authorized")
    if (approval["status"] != "APPROVED_PAIRED_TRAIN_OOF_ONLY" or approval["arms"] != ["v1", "v2"]
            or approval["stop_after_day4"] is not True
            or approval["candidate_manifest_sha256"] != config["candidate_manifest_sha256"]
            or approval["source_sidecar_manifest_sha256"] != config["source_sidecar_manifest_sha256"]
            or any(approval[k] is not False for k in ("validation_access", "test_access", "parameter_search",
                "threshold_search", "new_collection", "production_activation"))):
        raise ValueError("Approval does not match paired Train OOF scope")


def enroll_and_load(policy, root, profile, config):
    base = strict_path(root / config["source_sidecar_directory"])
    if not base.is_relative_to(root / "runs/phase2_p2_d4_day3"):
        raise ValueError("Only reviewed Day 3 Train sidecar storage allowed")
    manifest = base / "artifact_manifest.json"
    policy.enroll(manifest, config["source_sidecar_manifest_sha256"])
    entries = policy.json(manifest)["files"]
    indices = {}
    for item in entries:
        path = strict_path(base / item["path"])
        if not path.is_relative_to(base) or item["path"] in indices:
            raise ValueError("Duplicate or escaped sidecar member")
        indices[item["path"]] = item
    for name in ("summary.json", "feature_sidecars.json", "feature_eligibility_release.json", "run_context.json"):
        policy.enroll(base / name, indices[name]["sha256"])
    summary = policy.json(base / "summary.json")
    release = policy.json(base / "feature_eligibility_release.json")
    context = policy.json(base / "run_context.json")
    if (summary["gate"] != "PASS" or summary["observed"]["unknowns"] != 0
            or summary["observed"]["original_v1_feature_changes"] != 0
            or release["scope"] != "qualified_historical_train_v2_sidecars_only"
            or release["complete_v2_sidecars_qualified"] is not True
            or release["candidate_manifest_sha256"] != config["candidate_manifest_sha256"]
            or release["run_id"] != base.name or release["execution_manifest_sha256"] != context["execution_manifest_sha256"]
            or context["run_id"] != base.name or context["initial_git_status"] != ""):
        raise ValueError("Day 3 feature qualification failed")
    sidecars = policy.json(base / "feature_sidecars.json")
    if (release["sidecar_record_references"] != [s["sidecar_record_sha256"] for s in sidecars]
            or len({s["source_record_sha256"] for s in sidecars}) != len(sidecars)):
        raise ValueError("Sidecar qualification references mismatch or duplicate")
    enrollment = enroll_train(policy, root, profile)
    policy.seal()
    originals, proof = load_train(root, enrollment["proof_path"], profile["train_proof_sha256"])
    result = join_sidecars(originals, sidecars, config["candidate_manifest_sha256"])
    expected = config["expected_train"]
    actual = {"rows": len(result), "failures": sum(r["y_failure"] for r in result),
        "templates": len({r["template_id"] for r in result}),
        "new_feature_ones": sum(r["features"]["public_completion_unknown"] for r in result)}
    if actual != expected:
        raise ValueError("Train cohort differs from frozen comparison cohort")
    for name in policy.inputs: policy.verify(Path(name))
    policy.assert_clean()
    return result, {"train_source_proof": proof, "sidecar_manifest_sha256": config["source_sidecar_manifest_sha256"],
        "cohort": actual, "ordered_rows_sha256": canonical(result), "repeat_rows": 0,
        "censored_task_ids": proof["censored_task_ids"], "validation_test_used": False}


def join_sidecars(originals, sidecars, candidate_sha256):
    indexed = {}
    for s in sidecars:
        if (s["split"] != "train" or s["schema_version"] != "formal-decision-features-v2"
                or s["future_data_excluded"] is not True
                or canonical({k: v for k, v in s.items() if k != "sidecar_record_sha256"}) != s["sidecar_record_sha256"]
                or s["source_record_sha256"] in indexed or s["candidate_manifest_sha256"] != candidate_sha256
                or type(s["public_completion_unknown"]) is not int or s["public_completion_unknown"] not in (0, 1)):
            raise ValueError("Unqualified, unknown or duplicate v2 sidecar")
        indexed[s["source_record_sha256"]] = s
    if len(indexed) != len(originals) or set(indexed) != {r["risk_record_sha256"] for r in originals}:
        raise ValueError("v1/v2 primary cohorts differ")
    result = []
    for old in originals:
        s = indexed[old["risk_record_sha256"]]
        if (any(s[k] != old[k] for k in ("task_id", "template_id", "checkpoint_id", "checkpoint_record_sha256"))
                or s["cutoff_step"] != old["temporal"]["cutoff_step"]
                or s["original_v1_features_sha256"] != canonical(old["features"])
                or s["sources"] != old["sources"]
                or any(s["features"][n] != v for n, v in old["features"].items())
                or s["features"]["public_completion_unknown"] != s["public_completion_unknown"]
                or s["feature_names"] != [*old["features"], "public_completion_unknown"]):
            raise ValueError("Sidecar changed v1 values or original record lineage")
        result.append({"split": "train", "allowed_for_weight_fitting": True, "task_id": old["task_id"],
            "template_id": old["template_id"], "checkpoint_id": old["checkpoint_id"],
            "risk_record_sha256": old["risk_record_sha256"], "sidecar_record_sha256": s["sidecar_record_sha256"],
            "y_failure": old["y_failure"], "y_continue": 1 - old["y_failure"],
            "features": s["features"], "features_sha256": canonical(s["features"])})
    return result
