"""Bounded session-local records for image-grounded spatial queries."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

MatrixRows = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]


@dataclass(frozen=True)
class CaptureEvidence:
    capture_id: str
    scene_generation: int
    scene: str
    view_layer: str
    window_id: int
    target_name: str
    target_identity: str
    viewport_id: str
    view: str
    display_mode: str
    overlays: str
    width: int
    height: int
    native_sha256: str
    projection_kind: str
    clip_start: float
    clip_end: float
    view_matrix: MatrixRows
    projection_matrix: MatrixRows
    perspective_matrix: MatrixRows


class CaptureBook:
    """Keep only the most recent capture evidence for one add-on instance."""

    def __init__(self, limit: int = 32) -> None:
        if limit < 1:
            raise ValueError("capture evidence limit must be positive")
        self.limit = limit
        self._records: OrderedDict[str, CaptureEvidence] = OrderedDict()

    def add(self, evidence: CaptureEvidence) -> None:
        self._records[evidence.capture_id] = evidence
        self._records.move_to_end(evidence.capture_id)
        while len(self._records) > self.limit:
            self._records.popitem(last=False)

    def get(self, capture_id: str) -> CaptureEvidence | None:
        evidence = self._records.get(capture_id)
        if evidence is not None:
            self._records.move_to_end(capture_id)
        return evidence

    def clear(self) -> None:
        self._records.clear()

    def __len__(self) -> int:
        return len(self._records)
