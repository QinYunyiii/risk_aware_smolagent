"""Deterministic generation for the immutable v1 and additive v2 catalogs."""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping

from .schema import (
    DataSplit,
    Domain,
    FileFixture,
    TaskDefinitionError,
    TaskSpec,
    TaskTemplate,
    ToolStep,
)


@dataclass(frozen=True, slots=True)
class CatalogValidation:
    template_count: int
    instance_count: int
    templates_by_domain: Mapping[str, int]
    instances_by_domain: Mapping[str, int]
    templates_by_split: Mapping[str, int]
    instances_by_split: Mapping[str, int]
    templates_by_domain_split: Mapping[str, Mapping[str, int]]
    instances_by_domain_split: Mapping[str, Mapping[str, int]]
    checks: Mapping[str, bool]

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "passed": self.passed}


def _csv_content(fieldnames: list[str], rows: list[dict[str, object]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _task(
    template: TaskTemplate,
    instance_number: int,
    *,
    prompt: str,
    files: tuple[FileFixture, ...] = (),
    inventory: tuple[Mapping[str, Any], ...] = (),
    orders: tuple[Mapping[str, Any], ...] = (),
    steps: tuple[ToolStep, ...],
    expected: Mapping[str, Any],
) -> TaskSpec:
    instance_id = f"{template.template_id}_{instance_number:03d}"
    return TaskSpec(
        task_id=instance_id,
        template_id=template.template_id,
        instance_id=instance_id,
        domain=template.domain,
        seed=template.base_seed + instance_number,
        prompt=prompt,
        files=files,
        inventory=inventory,
        orders=orders,
        reference_steps=steps,
        expected=dict(expected),
        dataset_role="day7_catalog",
        split=template.split,
    )


def _planned_fault_type(parameters: Mapping[str, Any]) -> str:
    plan = parameters.get("fault_plan", {})
    if not isinstance(plan, Mapping):
        raise TaskDefinitionError("parameters.fault_plan must be a mapping")
    return str(plan.get("fault_type", "F0_NO_FAULT"))


def _generate_data(template: TaskTemplate, number: int) -> TaskSpec:
    seed = template.base_seed + number
    parameters = dict(template.parameters)
    family = template.family
    path = f"data/{template.template_id}_{number:03d}.csv"
    if family == "filter":
        column = str(parameters["column"])
        operator = str(parameters["operator"])
        if operator == "eq":
            target = f"group_{seed % 7}"
            other = f"group_{(seed + 3) % 7}"
            values: list[str] = [target, other, target, other]
            requested = target
        else:
            threshold = seed % 23 + 10
            values = [str(threshold - 3), str(threshold), str(threshold + 2), str(threshold + 8)]
            requested = str(threshold)
        rows = [
            {"record_id": f"r{index + 1}", column: value, "label": f"item_{number}_{index + 1}"}
            for index, value in enumerate(values)
        ]
        predicates: dict[str, Callable[[str], bool]] = {
            "eq": lambda value: value == requested,
            "gt": lambda value: Decimal(value) > Decimal(requested),
            "gte": lambda value: Decimal(value) >= Decimal(requested),
            "lt": lambda value: Decimal(value) < Decimal(requested),
            "lte": lambda value: Decimal(value) <= Decimal(requested),
        }
        if operator not in predicates:
            raise TaskDefinitionError(f"Unsupported generated filter operator: {operator}")
        expected_rows = [
            {key: str(value) for key, value in row.items()}
            for row in rows
            if predicates[operator](str(row[column]))
        ]
        return _task(
            template,
            number,
            prompt=f"Filter {path} where {column} {operator} {requested}; return every matching row.",
            files=(FileFixture(path, _csv_content(["record_id", column, "label"], rows)),),
            steps=(ToolStep("filter_data", {"path": path, "column": column, "operator": operator, "value": requested}),),
            expected={"result": expected_rows},
        )
    if family == "aggregate":
        operation = str(parameters["operation"])
        group_column = str(parameters["group_column"])
        value_column = str(parameters["value_column"])
        base = seed % 13 + 3
        rows = [
            {group_column: "alpha", value_column: base},
            {group_column: "beta", value_column: base + 2},
            {group_column: "alpha", value_column: base + 4},
            {group_column: "beta", value_column: base + 6},
        ]
        grouped = {"alpha": [base, base + 4], "beta": [base + 2, base + 6]}
        if operation == "sum":
            result: dict[str, float | int] = {key: float(sum(values)) for key, values in grouped.items()}
        elif operation == "mean":
            result = {key: float(sum(values) / len(values)) for key, values in grouped.items()}
        elif operation == "count":
            result = {key: len(values) for key, values in grouped.items()}
        else:
            raise TaskDefinitionError(f"Unsupported generated aggregate operation: {operation}")
        return _task(
            template,
            number,
            prompt=f"Compute {operation} of {value_column} grouped by {group_column} in {path}.",
            files=(FileFixture(path, _csv_content([group_column, value_column], rows)),),
            steps=(ToolStep("aggregate_data", {"path": path, "group_by": group_column, "value_column": value_column, "operation": operation}),),
            expected={"result": result},
        )
    if family == "multi_data":
        filter_column = str(parameters["filter_column"])
        operator = str(parameters["operator"])
        group_by = str(parameters["group_by"])
        value_column = str(parameters["value_column"])
        operation = str(parameters["operation"])
        target = (
            f"segment_{seed % 3}"
            if operator == "eq"
            else str(seed % 9 + 5)
        )
        rows = [
            {
                "record_id": f"r{index}",
                "category": f"segment_{(seed + index) % 3}",
                "region": ("north", "south")[index % 2],
                "amount": seed % 9 + index + 2,
                "units": seed % 7 + index + 1,
                "latency": seed % 11 + index + 3,
            }
            for index in range(1, 7)
        ]
        predicates: dict[str, Callable[[str], bool]] = {
            "eq": lambda value: value == target,
            "gt": lambda value: Decimal(value) > Decimal(target),
            "gte": lambda value: Decimal(value) >= Decimal(target),
            "lt": lambda value: Decimal(value) < Decimal(target),
            "lte": lambda value: Decimal(value) <= Decimal(target),
        }
        if operator not in predicates:
            raise TaskDefinitionError(f"Unsupported generated filter operator: {operator}")
        filtered = [
            {key: str(value) for key, value in row.items()}
            for row in rows
            if predicates[operator](str(row[filter_column]))
        ]
        grouped: dict[str, list[Decimal]] = {}
        for row in filtered:
            grouped.setdefault(row[group_by], []).append(Decimal(row[value_column]))
        if operation == "sum":
            result = {
                key: float(sum(values, Decimal(0)))
                for key, values in sorted(grouped.items())
            }
        elif operation == "mean":
            result = {
                key: float(sum(values, Decimal(0)) / len(values))
                for key, values in sorted(grouped.items())
            }
        elif operation == "count":
            result = {key: len(values) for key, values in sorted(grouped.items())}
        else:
            raise TaskDefinitionError(f"Unsupported generated aggregate operation: {operation}")
        filter_arguments = {
            "records": {"$ref": "step_1"},
            "column": filter_column,
            "operator": operator,
            "value": target,
        }
        steps: list[ToolStep] = [
            ToolStep("read_csv", {"path": path}),
            ToolStep("filter_records", filter_arguments),
        ]
        aggregate_source = 2
        if _planned_fault_type(parameters) == "F5_REPEATED_ACTION":
            steps.append(ToolStep("filter_records", filter_arguments))
            aggregate_source = 3
        steps.append(
            ToolStep(
                "aggregate_records",
                {
                    "records": {"$ref": f"step_{aggregate_source}"},
                    "group_by": group_by,
                    "value_column": value_column,
                    "operation": operation,
                },
            )
        )
        return _task(
            template,
            number,
            prompt=(
                f"Read {path}, filter the returned records where {filter_column} "
                f"{operator} {target}, then compute {operation} of {value_column} "
                f"grouped by {group_by}. Use each preceding tool result as the next input."
            ),
            files=(
                FileFixture(
                    path,
                    _csv_content(
                        ["record_id", "category", "region", "amount", "units", "latency"],
                        rows,
                    ),
                ),
            ),
            steps=tuple(steps),
            expected={"result": result},
        )
    raise TaskDefinitionError(f"Unsupported data template family: {family}")


def _generate_file(template: TaskTemplate, number: int) -> TaskSpec:
    seed = template.base_seed + number
    parameters = dict(template.parameters)
    family = template.family
    directory = f"workspace/{template.template_id}_{number:03d}"
    if family == "list":
        nested = bool(parameters.get("nested", False))
        relative_paths = [
            f"{directory}/alpha_{seed % 17}.txt",
            f"{directory}/beta_{seed % 19}.md",
            f"{directory}/nested/gamma_{seed % 23}.txt" if nested else f"{directory}/gamma_{seed % 23}.txt",
        ]
        fixtures = tuple(FileFixture(path, f"fixture {index}\n") for index, path in enumerate(relative_paths, start=1))
        return _task(
            template,
            number,
            prompt=f"List every file recursively under {directory}.",
            files=fixtures,
            steps=(ToolStep("list_files", {"directory": directory}),),
            expected={"result": sorted(relative_paths)},
        )
    if family == "search":
        query = f"Signal {seed % 997}"
        match_count = int(parameters.get("match_count", 1))
        casefold = bool(parameters.get("casefold", False))
        line_number = int(parameters.get("line_number", 2))
        fixtures: list[FileFixture] = []
        expected: list[dict[str, object]] = []
        for index in range(1, 4):
            path = f"{directory}/document_{index}.txt"
            lines = [f"Header {index}", "Routine text", f"Footer {index}"]
            if index <= match_count:
                text = f"{query} appears in document {index}."
                if casefold and index % 2:
                    text = text.swapcase()
                insertion = max(1, min(line_number, len(lines))) - 1
                lines[insertion] = text
                expected.append({"path": path, "line_number": insertion + 1, "text": text})
            fixtures.append(FileFixture(path, "\n".join(lines) + "\n"))
        return _task(
            template,
            number,
            prompt=f"Find every line under {directory} containing {query}, case-insensitively.",
            files=tuple(fixtures),
            steps=(ToolStep("search_files", {"directory": directory, "query": query}),),
            expected={"result": expected},
        )
    if family == "read":
        style = str(parameters.get("style", "exact"))
        path = f"{directory}/target.txt"
        if style == "unicode":
            content = f"负责人：林{number}\n状态：已验证 ✓\n编号：{seed}\n"
        elif style == "multiline":
            content = f"Title {number}\nOwner: Team {seed % 11}\nStatus: ready\nNotes: preserve spacing.\n"
        else:
            content = f"Record {seed}\nOwner: analyst_{number}\n"
        fixtures = (
            FileFixture(path, content),
            FileFixture(f"{directory}/distractor.txt", "This is not the requested file.\n"),
        )
        return _task(
            template,
            number,
            prompt=f"Read {path} and return its exact contents.",
            files=fixtures,
            steps=(ToolStep("read_file", {"path": path}),),
            expected={"result": content},
        )
    if family == "multi_file":
        query = f"Key-{seed % 997}"
        target_path = f"{directory}/reports/target_{number}.txt"
        target_content = (
            f"Report {number}\nLookup: {query}\nOwner: team-{seed % 13}\n"
        )
        fixtures = (
            FileFixture(target_path, target_content),
            FileFixture(f"{directory}/reports/other.txt", "No matching key here.\n"),
            FileFixture(f"{directory}/archive/note.md", f"Archive reference {seed}.\n"),
        )
        search_arguments = {
            "paths": {"$ref": "step_1"},
            "query": query,
        }
        steps: list[ToolStep] = [
            ToolStep("list_files", {"directory": directory}),
            ToolStep("search_paths", search_arguments),
        ]
        search_source = 2
        if _planned_fault_type(parameters) == "F5_REPEATED_ACTION":
            steps.append(ToolStep("search_paths", search_arguments))
            search_source = 3
        steps.append(
            ToolStep(
                "read_file",
                {"path": {"$ref": f"step_{search_source}.0.path"}},
            )
        )
        return _task(
            template,
            number,
            prompt=(
                f"List files under {directory}, search only those returned paths for "
                f"{query}, then read the first matched file and return its exact content."
            ),
            files=fixtures,
            steps=tuple(steps),
            expected={"result": target_content},
        )
    raise TaskDefinitionError(f"Unsupported file template family: {family}")


def _generate_api(template: TaskTemplate, number: int) -> TaskSpec:
    seed = template.base_seed + number
    parameters = dict(template.parameters)
    family = template.family
    if family == "inventory":
        sku = f"sku-{seed}"
        quantity = seed % 50 + int(parameters.get("quantity_offset", 1))
        inventory = (
            {"sku": sku, "quantity": quantity},
            {"sku": f"other-{seed}", "quantity": quantity + 7},
        )
        return _task(
            template,
            number,
            prompt=f"Return the exact current inventory for {sku}.",
            inventory=inventory,
            steps=(ToolStep("query_inventory", {"sku": sku}),),
            expected={"result": {"sku": sku, "quantity": quantity}, "database": {"inventory": {sku: {"quantity": quantity}}}},
        )
    order_id = f"order-{seed}"
    initial_status = str(parameters["initial_status"])
    orders = ({"order_id": order_id, "status": initial_status, "version": 1},)
    if family == "get_order":
        result = {"order_id": order_id, "status": initial_status, "version": 1}
        return _task(
            template,
            number,
            prompt=f"Return the current state of {order_id}.",
            orders=orders,
            steps=(ToolStep("get_order", {"order_id": order_id}),),
            expected={"result": result, "database": {"orders": {order_id: {"status": initial_status, "version": 1}}}},
        )
    if family == "update_order":
        new_status = str(parameters["new_status"])
        idempotency_key = f"{template.template_id}-{number:03d}"
        arguments = {"order_id": order_id, "expected_status": initial_status, "new_status": new_status, "idempotency_key": idempotency_key}
        result = {"order_id": order_id, "status": new_status, "version": 2, "idempotent_replay": False}
        return _task(
            template,
            number,
            prompt=f"Move {order_id} from {initial_status} to {new_status} exactly once.",
            orders=orders,
            steps=(ToolStep("update_order", arguments),),
            expected={"result": result, "database": {"orders": {order_id: {"status": new_status, "version": 2}}}},
        )
    if family == "multi_api":
        new_status = str(parameters["new_status"])
        idempotency_key = f"{template.template_id}-{number:03d}"
        fault_type = _planned_fault_type(parameters)
        steps: list[ToolStep] = [ToolStep("get_order", {"order_id": order_id})]
        state_source = 1
        if fault_type in {"F4_STALE_OBSERVATION", "F5_REPEATED_ACTION"}:
            steps.append(ToolStep("get_order", {"order_id": order_id}))
            state_source = 2
        steps.append(
            ToolStep(
                "update_order",
                {
                    "order_id": {"$ref": f"step_{state_source}.order_id"},
                    "expected_status": {"$ref": f"step_{state_source}.status"},
                    "new_status": new_status,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        update_source = len(steps)
        steps.append(
            ToolStep(
                "get_order",
                {"order_id": {"$ref": f"step_{update_source}.order_id"}},
            )
        )
        result = {"order_id": order_id, "status": new_status, "version": 2}
        return _task(
            template,
            number,
            prompt=(
                f"Read {order_id}, use the observed current status to move it to "
                f"{new_status} with idempotency key {idempotency_key}, then query it "
                "again and return the verified final state."
            ),
            orders=orders,
            steps=tuple(steps),
            expected={
                "result": result,
                "database": {
                    "orders": {order_id: {"status": new_status, "version": 2}}
                },
            },
        )
    raise TaskDefinitionError(f"Unsupported API template family: {family}")


def _calculate_expression(kind: str, seed: int) -> tuple[str, str]:
    a = seed % 17 + 3
    b = seed % 7 + 2
    c = seed % 5 + 2
    if kind == "addition":
        return f"{a} + {b}", str(a + b)
    if kind == "subtraction":
        return f"{a + b} - {b}", str(a)
    if kind == "multiplication":
        return f"{a} * {b}", str(a * b)
    if kind == "division":
        return f"{a * b} / {b}", str(a)
    if kind == "parentheses":
        return f"({a} + {b}) * {c}", str((a + b) * c)
    if kind == "power":
        exponent = seed % 3 + 2
        base = seed % 5 + 2
        return f"{base} ** {exponent}", str(base**exponent)
    raise TaskDefinitionError(f"Unsupported calculator kind: {kind}")


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _generate_logic(template: TaskTemplate, number: int) -> TaskSpec:
    seed = template.base_seed + number
    parameters = dict(template.parameters)
    family = template.family
    if family == "calculator":
        expression, result = _calculate_expression(str(parameters["kind"]), seed)
        return _task(
            template,
            number,
            prompt=f"Calculate {expression} exactly.",
            steps=(ToolStep("calculator", {"expression": expression}),),
            expected={"result": result},
        )
    if family == "unit_conversion":
        from_unit = str(parameters["from_unit"])
        to_unit = str(parameters["to_unit"])
        factor = Decimal(str(parameters["factor"]))
        value = Decimal(seed % 90 + 10)
        result = _format_decimal(value * factor)
        return _task(
            template,
            number,
            prompt=f"Convert {value} {from_unit} to {to_unit}.",
            steps=(ToolStep("unit_converter", {"value": str(value), "from_unit": from_unit, "to_unit": to_unit}),),
            expected={"result": result},
        )
    if family == "validation":
        expected_value = Decimal(seed % 20 + 10)
        tolerance = Decimal("0.5")
        actual = expected_value + (Decimal("0.25") if number % 2 else Decimal("0.75"))
        result = abs(actual - expected_value) <= tolerance
        arguments = {"actual": str(actual), "expected": str(expected_value), "tolerance": str(tolerance)}
        return _task(
            template,
            number,
            prompt=f"Check whether {actual} is within {tolerance} of {expected_value}.",
            steps=(ToolStep("validate_result", arguments),),
            expected={"result": result},
        )
    if family == "multi_logic":
        base = seed % 17 + 3
        increment = seed % 7 + 2
        factor = int(parameters.get("factor", 2))
        first_expression = f"{base} + {increment}"
        first_result = base + increment
        fault_type = _planned_fault_type(parameters)
        steps: list[ToolStep] = [
            ToolStep("calculator", {"expression": first_expression})
        ]
        source_step = 1
        if fault_type == "F5_REPEATED_ACTION":
            steps.append(ToolStep("calculator", {"expression": first_expression}))
            source_step = 2
        steps.append(
            ToolStep(
                "calculator",
                {"expression": {"$template": f"{{step_{source_step}}} * {factor}"}},
            )
        )
        calculated_step = len(steps)
        expected_result = str(first_result * factor)
        steps.append(
            ToolStep(
                "validate_result",
                {
                    "actual": {"$ref": f"step_{calculated_step}"},
                    "expected": expected_result,
                    "tolerance": "0",
                },
            )
        )
        return _task(
            template,
            number,
            prompt=(
                f"Calculate {first_expression}, multiply that returned result by {factor} "
                "in a second calculation, then validate the final value exactly."
            ),
            steps=tuple(steps),
            expected={"result": True},
        )
    raise TaskDefinitionError(f"Unsupported logic template family: {family}")


_GENERATORS: dict[Domain, Callable[[TaskTemplate, int], TaskSpec]] = {
    Domain.DATA_ANALYSIS: _generate_data,
    Domain.FILE_RETRIEVAL: _generate_file,
    Domain.API_WORKFLOW: _generate_api,
    Domain.CALCULATION_LOGIC: _generate_logic,
}


def generate_task_instances(
    templates: tuple[TaskTemplate, ...], instances_per_template: int = 5
) -> tuple[TaskSpec, ...]:
    if instances_per_template not in {2, 5}:
        raise TaskDefinitionError("Catalog generation supports exactly two or five instances")
    tasks = tuple(
        _GENERATORS[template.domain](template, number)
        for template in templates
        for number in range(1, instances_per_template + 1)
    )
    task_ids = [task.task_id for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise TaskDefinitionError("Generated task_id values must be unique")
    return tasks


def validate_task_catalog(
    templates: tuple[TaskTemplate, ...], tasks: tuple[TaskSpec, ...]
) -> CatalogValidation:
    templates_by_domain = Counter(template.domain.value for template in templates)
    instances_by_domain = Counter(task.domain.value for task in tasks)
    templates_by_split = Counter(template.split.value for template in templates)
    instances_by_split = Counter(task.split.value for task in tasks if task.split is not None)
    templates_by_domain_split = {
        domain.value: {
            split.value: sum(
                template.domain == domain and template.split == split
                for template in templates
            )
            for split in DataSplit
        }
        for domain in Domain
    }
    instances_by_domain_split = {
        domain.value: {
            split.value: sum(
                task.domain == domain and task.split == split for task in tasks
            )
            for split in DataSplit
        }
        for domain in Domain
    }
    families_by_domain: dict[Domain, set[str]] = {
        domain: {template.family for template in templates if template.domain == domain}
        for domain in Domain
    }
    train_families_by_domain: dict[Domain, set[str]] = {
        domain: {
            template.family
            for template in templates
            if template.domain == domain and template.split == DataSplit.TRAIN
        }
        for domain in Domain
    }
    template_splits: dict[str, set[DataSplit | None]] = {}
    template_instance_counts: Counter[str] = Counter()
    for task in tasks:
        template_splits.setdefault(task.template_id, set()).add(task.split)
        template_instance_counts[task.template_id] += 1
    checks = {
        "forty_templates": len(templates) == 40,
        "two_hundred_instances": len(tasks) == 200,
        "ten_templates_per_domain": set(templates_by_domain.values()) == {10}
        and len(templates_by_domain) == 4,
        "fifty_instances_per_domain": set(instances_by_domain.values()) == {50}
        and len(instances_by_domain) == 4,
        "template_split_24_8_8": dict(templates_by_split)
        == {"train": 24, "validation": 8, "test": 8},
        "instance_split_120_40_40": dict(instances_by_split)
        == {"train": 120, "validation": 40, "test": 40},
        "per_domain_template_split_6_2_2": all(
            counts == {"train": 6, "validation": 2, "test": 2}
            for counts in templates_by_domain_split.values()
        ),
        "per_domain_instance_split_30_10_10": all(
            counts == {"train": 30, "validation": 10, "test": 10}
            for counts in instances_by_domain_split.values()
        ),
        "all_tool_families_represented_in_train": all(
            train_families_by_domain[domain] == families_by_domain[domain]
            for domain in Domain
        ),
        "five_instances_per_template": set(template_instance_counts.values()) == {5}
        and len(template_instance_counts) == 40,
        "no_template_crosses_split": all(
            len(splits) == 1 and None not in splits for splits in template_splits.values()
        ),
        "unique_task_instance_ids": len({task.task_id for task in tasks}) == len(tasks)
        and len({task.instance_id for task in tasks}) == len(tasks),
        "all_catalog_roles": all(task.dataset_role == "day7_catalog" for task in tasks),
        "all_have_deterministic_expectations": all(
            bool(task.expected) and bool(task.reference_steps) for task in tasks
        ),
    }
    return CatalogValidation(
        template_count=len(templates),
        instance_count=len(tasks),
        templates_by_domain=dict(sorted(templates_by_domain.items())),
        instances_by_domain=dict(sorted(instances_by_domain.items())),
        templates_by_split=dict(sorted(templates_by_split.items())),
        instances_by_split=dict(sorted(instances_by_split.items())),
        templates_by_domain_split=templates_by_domain_split,
        instances_by_domain_split=instances_by_domain_split,
        checks=checks,
    )


def task_to_dict(task: TaskSpec) -> dict[str, object]:
    raw = asdict(task)
    raw["domain"] = task.domain.value
    raw["split"] = task.split.value if task.split is not None else None
    return raw
