"""Deterministic calculation, conversion, and result validation tools."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .calculation_tools import safe_calculate


class LogicToolError(ValueError):
    pass


_UNIT_FACTORS: dict[tuple[str, str], Decimal] = {
    ("m", "km"): Decimal("0.001"),
    ("km", "m"): Decimal("1000"),
    ("g", "kg"): Decimal("0.001"),
    ("kg", "g"): Decimal("1000"),
    ("min", "s"): Decimal("60"),
    ("h", "min"): Decimal("60"),
}


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


class LogicTools:
    def calculator(self, expression: str) -> str:
        return safe_calculate(expression)

    def unit_converter(self, value: str, from_unit: str, to_unit: str) -> str:
        try:
            numeric = Decimal(value)
        except InvalidOperation as exc:
            raise LogicToolError(f"Invalid decimal value: {value!r}") from exc
        if not numeric.is_finite():
            raise LogicToolError("Non-finite values are not allowed")
        try:
            factor = _UNIT_FACTORS[(from_unit, to_unit)]
        except KeyError as exc:
            raise LogicToolError(f"Unsupported conversion: {from_unit} -> {to_unit}") from exc
        return _format_decimal(numeric * factor)

    def validate_result(
        self,
        actual: str,
        expected: str,
        tolerance: str = "0",
    ) -> bool:
        try:
            actual_value = Decimal(actual)
            expected_value = Decimal(expected)
            tolerance_value = Decimal(tolerance)
            if not all(value.is_finite() for value in (actual_value, expected_value, tolerance_value)):
                raise LogicToolError("Non-finite validation values are not allowed")
            if tolerance_value < 0:
                raise LogicToolError("tolerance must be non-negative")
            return abs(actual_value - expected_value) <= tolerance_value
        except InvalidOperation:
            return actual == expected
