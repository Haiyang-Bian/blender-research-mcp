"""Public schema helpers for transactional Mesh object separation."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

MeshObjectName = Annotated[str, Field(min_length=1, max_length=255)]
