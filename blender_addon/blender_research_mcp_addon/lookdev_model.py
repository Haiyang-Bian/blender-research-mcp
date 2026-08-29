"""Pure validation for bounded LookDev property values."""

from __future__ import annotations

import math
from typing import Any

PropertyValue = bool | int | float | tuple[float, ...]


class LookdevModelError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def normalize_material_value(
    socket_kind: str,
    raw_value: Any,
    *,
    minimum: float | None,
    maximum: float | None,
) -> PropertyValue:
    if socket_kind == "BOOLEAN":
        if type(raw_value) is not bool:
            raise LookdevModelError(
                "MATERIAL_SOCKET_TYPE_MISMATCH",
                "Boolean sockets require a JSON boolean",
            )
        return raw_value
    if socket_kind == "INT":
        if type(raw_value) is not int:
            raise LookdevModelError(
                "MATERIAL_SOCKET_TYPE_MISMATCH",
                "Integer sockets require a JSON integer",
            )
        _require_range(float(raw_value), minimum, maximum)
        return raw_value
    if socket_kind == "FLOAT":
        if type(raw_value) is not float or not math.isfinite(raw_value):
            raise LookdevModelError(
                "MATERIAL_SOCKET_TYPE_MISMATCH",
                "Float sockets require a finite JSON floating-point value",
            )
        _require_range(raw_value, minimum, maximum)
        return raw_value
    dimensions = {"VECTOR": 3, "COLOR": 4}
    expected_length = dimensions.get(socket_kind)
    if expected_length is None:
        raise LookdevModelError(
            "MATERIAL_SOCKET_UNSUPPORTED",
            f"Unsupported material socket kind: {socket_kind}",
        )
    if not isinstance(raw_value, (list, tuple)) or len(raw_value) != expected_length:
        raise LookdevModelError(
            "MATERIAL_SOCKET_TYPE_MISMATCH",
            f"{socket_kind} sockets require {expected_length} floating-point components",
        )
    if any(type(component) is not float or not math.isfinite(component) for component in raw_value):
        raise LookdevModelError(
            "MATERIAL_SOCKET_TYPE_MISMATCH",
            f"{socket_kind} components must be finite JSON floating-point values",
        )
    value = tuple(raw_value)
    for component in value:
        _require_range(component, minimum, maximum)
    return value


def _require_range(
    value: float,
    minimum: float | None,
    maximum: float | None,
) -> None:
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise LookdevModelError(
            "MATERIAL_SOCKET_VALUE_OUT_OF_RANGE",
            "Material socket value is outside the existing socket range",
            details={"minimum": minimum, "maximum": maximum, "value": value},
        )
