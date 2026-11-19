"""Deterministic CSV tools scoped to one task workspace."""

from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from risk_agent.benchmark.environment import TaskEnvironment


class DataAnalysisError(ValueError):
    pass


def _decimal(value: str) -> Decimal:
    try:
        numeric = Decimal(value)
    except InvalidOperation as exc:
        raise DataAnalysisError(f"Expected a numeric value, got {value!r}") from exc
    if not numeric.is_finite():
        raise DataAnalysisError("Non-finite numeric values are not allowed")
    return numeric


class DataAnalysisTools:
    def __init__(self, environment: TaskEnvironment) -> None:
        self.environment = environment

    def read_csv(self, path: str) -> list[dict[str, str]]:
        source = self.environment.resolve_path(path)
        if source.suffix.lower() != ".csv":
            raise DataAnalysisError("read_csv only accepts .csv files")
        with source.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise DataAnalysisError("CSV file has no header")
            return [dict(row) for row in reader]

    def filter_data(
        self,
        path: str,
        column: str,
        operator: str,
        value: str,
    ) -> list[dict[str, str]]:
        rows = self.read_csv(path)
        if rows and column not in rows[0]:
            raise DataAnalysisError(f"Unknown column: {column}")
        if operator == "eq":
            predicate = lambda item: item == value
        elif operator in {"gt", "gte", "lt", "lte"}:
            target = _decimal(value)
            predicates = {
                "gt": lambda item: _decimal(item) > target,
                "gte": lambda item: _decimal(item) >= target,
                "lt": lambda item: _decimal(item) < target,
                "lte": lambda item: _decimal(item) <= target,
            }
            predicate = predicates[operator]
        else:
            raise DataAnalysisError(f"Unsupported filter operator: {operator}")
        return [row for row in rows if predicate(row[column])]

    def aggregate_data(
        self,
        path: str,
        group_by: str,
        value_column: str,
        operation: str,
    ) -> dict[str, float | int]:
        rows = self.read_csv(path)
        if rows and (group_by not in rows[0] or value_column not in rows[0]):
            raise DataAnalysisError("Unknown group or value column")
        values: dict[str, list[Decimal]] = defaultdict(list)
        for row in rows:
            values[row[group_by]].append(_decimal(row[value_column]))
        result: dict[str, float | int] = {}
        for key in sorted(values):
            group = values[key]
            if operation == "sum":
                result[key] = float(sum(group, Decimal(0)))
            elif operation == "mean":
                result[key] = float(sum(group, Decimal(0)) / len(group))
            elif operation == "count":
                result[key] = len(group)
            else:
                raise DataAnalysisError(f"Unsupported aggregate operation: {operation}")
        return result
