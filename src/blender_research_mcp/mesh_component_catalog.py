"""Closed schemas for revision-bound connected FACE component catalogs."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

ComponentCatalogId = Annotated[str, Field(min_length=1, max_length=128)]
ComponentIdentity = Annotated[str, Field(min_length=1, max_length=128)]
ComponentCatalogMetric = Literal[
    "COUNT",
    "AREA",
    "BOUNDS",
    "MATERIALS",
    "BOUNDARY_COUNT",
]
ComponentCatalogMetrics = Annotated[
    tuple[ComponentCatalogMetric, ...], Field(min_length=1, max_length=5)
]
ComponentIdentities = Annotated[
    tuple[ComponentIdentity, ...], Field(min_length=1, max_length=4096)
]

DEFAULT_COMPONENT_CATALOG_METRICS: ComponentCatalogMetrics = (
    "COUNT",
    "AREA",
    "BOUNDS",
    "MATERIALS",
    "BOUNDARY_COUNT",
)


__all__ = [
    "ComponentCatalogId",
    "ComponentCatalogMetric",
    "ComponentCatalogMetrics",
    "ComponentIdentities",
    "ComponentIdentity",
    "DEFAULT_COMPONENT_CATALOG_METRICS",
]
