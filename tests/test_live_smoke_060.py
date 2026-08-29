from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_smoke_060.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("live_smoke_060", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_candidate_values_are_bounded_distinct_and_avoid_baseline() -> None:
    values = MODULE.candidate_values(0.4, 0.0, 1.0, count=3)

    assert len(values) == 3
    assert len(set(values)) == 3
    assert all(0.0 <= value <= 1.0 for value in values)
    assert 0.4 not in values


@pytest.mark.parametrize(
    ("baseline", "minimum", "maximum"),
    [(0.0, 0.0, 0.0), (2.0, 0.0, 1.0), (float("nan"), 0.0, 1.0)],
)
def test_candidate_values_reject_unusable_ranges(
    baseline: float,
    minimum: float,
    maximum: float,
) -> None:
    with pytest.raises(RuntimeError):
        MODULE.candidate_values(baseline, minimum, maximum)
