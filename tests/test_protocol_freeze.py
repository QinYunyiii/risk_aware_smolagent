from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from risk_agent.benchmark import (
    ReferenceTaskRunner,
    TaskEnvironment,
    generate_task_instances,
    load_task_templates,
    task_to_dict,
)
from risk_agent.benchmark.verifier import DeterministicVerifier
from risk_agent.collection.day5_gate import canonical_task_catalog_hash, split_manifest_hash
from risk_agent.collection.protocol_freeze import (
    CURRENT_MANIFEST_PATH as MANIFEST_PATH,
    CURRENT_PROFILE_PATH as PROFILE_PATH,
    ORIGINAL_REVISION,
    ProtocolFreezeError,
    build_formal_catalog,
    load_frozen_collection,
    render_exact_identifier_prompt,
)

ROOT = Path(__file__).resolve().parents[1]


def test_original_freeze_is_preserved_and_rejects_the_revised_implementation() -> None:
    with pytest.raises(ProtocolFreezeError, match="Frozen input changed"):
        load_frozen_collection(ROOT, implementation_revision=ORIGINAL_REVISION)


def test_frozen_profile_checks_actual_sources_and_remains_collection_locked() -> None:
    frozen = load_frozen_collection(ROOT)
    summary = frozen.audit_summary()
    assert summary["gate"] == "PROTOCOL_FREEZE_LOCAL_PASS"
    assert summary["formal_collection_ready"] is False
    assert summary["tasks_by_split"] == {"train": 96, "validation": 32, "test": 32}
    assert summary["max_completion_tokens_per_call"] == 1024
    old = yaml.safe_load((ROOT / "configs/phase2_risk_collection.yaml").read_text())
    new = frozen.config
    assert old["limits"]["max_completion_tokens_per_call"] == 256
    assert new["limits"] == {**old["limits"], "max_completion_tokens_per_call": 1024}
    for section in ("labels", "sampling", "storage", "quality_gates", "evaluation"):
        assert new[section] == old[section]
    assert new["capture"]["primary_rule"] == old["capture"]["primary_rule"]
    assert new["continue"]["carry_source_remaining_budget"] is True
    assert new["continue"]["reset_budget_after_restore"] is False


def test_prompt_revision_preserves_all_non_prompt_fields_and_old_templates() -> None:
    templates = load_task_templates(ROOT / "configs/task_templates_v2.yaml")
    originals = generate_task_instances(templates, 2)
    revised = build_formal_catalog(ROOT)
    changed = []
    for old, new in zip(originals, revised, strict=True):
        assert task_to_dict(replace(new, prompt=old.prompt)) == task_to_dict(old)
        if old.prompt != new.prompt:
            changed.append(new)
            assert new.template_id.startswith("p2_api_")
            assert json.dumps(new.orders[0]["order_id"]) in new.prompt
            number = int(new.instance_id.rsplit("_", 1)[1])
            assert json.dumps(f"{new.template_id}-{number:03d}") in new.prompt
    assert len(changed) == 20
    assert len({task.template_id for task in changed}) == 10
    assert Counter(task.split.value for task in changed) == {"train": 12, "validation": 4, "test": 4}
    assert split_manifest_hash(originals) == split_manifest_hash(revised)
    assert canonical_task_catalog_hash(originals) != canonical_task_catalog_hash(revised)
    groups = defaultdict(set)
    for task in revised:
        groups[task.template_id].add(task.split)
    assert len(groups) == 80 and all(len(splits) == 1 for splits in groups.values())


def test_renderer_does_not_use_hidden_expected_reference_or_fault_data() -> None:
    task = next(t for t in build_formal_catalog(ROOT) if t.task_id == "p2_api_process_control_001")
    template = next(t for t in load_task_templates(ROOT / "configs/task_templates_v2.yaml")
                    if t.template_id == task.template_id)
    changed_task = replace(
        task, expected={"hidden": "do-not-expose"},
        reference_steps=(replace(task.reference_steps[0], arguments={"hidden": "different"}),),
    )
    changed_template = replace(template, parameters={**template.parameters, "fault_plan": {"hidden": "secret"}})
    assert render_exact_identifier_prompt(changed_task, changed_template) == task.prompt


@pytest.mark.parametrize("relative", [
    PROFILE_PATH,
    "configs/task_catalog_v2_1.yaml",
    "configs/task_templates_v2.yaml",
    "configs/remote_model_p2_d2_1024.yaml",
    "docs/phase2/checkpoint_schema_v2.json",
    "docs/phase2/task_split_manifest_v2_1.json",
    "docs/phase2/failure_taxonomy_v2.md",
    "src/risk_agent/benchmark/verifier.py",
    "src/risk_agent/collection/protocol_freeze.py",
    "src/risk_agent/collection/continue_gate.py",
    "src/risk_agent/agent/checkpointable_tool_agent.py",
])
def test_freeze_rejects_drift_before_catalog_loading(tmp_path: Path, relative: str) -> None:
    manifest = json.loads((ROOT / MANIFEST_PATH).read_text())
    for name in [*manifest["files"], MANIFEST_PATH]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    with (tmp_path / relative).open("ab") as handle:
        handle.write(b"\n# unexpected drift\n")
    with pytest.raises(ProtocolFreezeError, match="Frozen input changed"):
        load_frozen_collection(tmp_path)


def test_all_revised_tasks_pass_reference_and_reject_wrong_results(tmp_path: Path) -> None:
    tasks = build_formal_catalog(ROOT)
    runner = ReferenceTaskRunner(tmp_path / "reference")
    for task in tasks:
        result = runner.run(task)
        assert result.passed and result.reset_verified, task.task_id
        environment = TaskEnvironment(tmp_path / "negative", task)
        environment.reset()
        assert not DeterministicVerifier().verify(task, environment, {"deliberately": "wrong"}).passed
