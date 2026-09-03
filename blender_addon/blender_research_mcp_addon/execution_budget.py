"""Call-local cooperative deadlines; never accepted from command parameters."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_deadline: ContextVar[float | None] = ContextVar("mesh_execution_deadline", default=None)


@contextmanager
def execution_deadline(deadline: float) -> Iterator[None]:
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def deadline_after(seconds: float) -> float:
    return min(_deadline.get() or float("inf"), time.monotonic() + seconds)


def check_deadline(reserve_seconds: float = 0.0) -> None:
    deadline = _deadline.get()
    if deadline is not None and time.monotonic() + reserve_seconds >= deadline:
        from .mesh_ops import MeshOperationError

        raise MeshOperationError(
            "MESH_BUDGET_EXCEEDED",
            "Mesh operation reached its execution deadline",
            kind="timeout",
            details={"reason": "EXECUTION_DEADLINE", "phase": "execution"},
        )
