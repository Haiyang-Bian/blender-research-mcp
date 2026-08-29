"""Run the repository's required Python quality gates in a fixed order."""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_GATES: tuple[tuple[str, ...], ...] = (
    ("uv", "run", "--no-sync", "pytest"),
    ("uv", "run", "--no-sync", "ruff", "check", "."),
    ("uv", "run", "--no-sync", "mypy"),
)


def gate_commands(pytest_targets: Sequence[str] = ()) -> tuple[tuple[str, ...], ...]:
    pytest_command = BASE_GATES[0] + tuple(pytest_targets)
    return (pytest_command, *BASE_GATES[1:])


def run_quality_gate(pytest_targets: Sequence[str] = ()) -> int:
    for command in gate_commands(pytest_targets):
        print(f"+ {' '.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            return completed.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pytest_target",
        nargs="*",
        help="optional pytest path or node id; Ruff and mypy still run afterward",
    )
    args = parser.parse_args()
    return run_quality_gate(args.pytest_target)


if __name__ == "__main__":
    raise SystemExit(main())
