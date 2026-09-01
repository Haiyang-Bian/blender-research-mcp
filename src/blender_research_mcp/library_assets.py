"""Closed schemas and local-file evidence for controlled Blender Library append."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from blender_research_mcp.errors import ErrorKind, bridge_error
from blender_research_mcp.scene_organization import CollectionParent

LibraryKind = Literal["OBJECT", "COLLECTION", "MESH"]
LibraryName = Annotated[str, Field(min_length=1, max_length=255)]
LibraryPath = Annotated[str, Field(min_length=1, max_length=32767)]
LibraryDigest = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
LibraryIdentity = Annotated[str, Field(min_length=1, max_length=128)]
LibraryFingerprint = Annotated[str, Field(min_length=64, max_length=64)]


class LibrarySource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: LibraryPath
    expected_file_sha256: LibraryDigest
    expected_size_bytes: Annotated[StrictInt, Field(ge=12)]


class LibraryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: LibraryKind
    name: LibraryName
    expected_entry_identity: LibraryDigest


class LibraryCollectionDestination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection_name: LibraryName
    expected_collection_identity: LibraryIdentity
    expected_collection_structure_fingerprint: LibraryFingerprint


class LibraryObjectOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["OBJECT"]
    new_object_name: LibraryName
    collection: LibraryCollectionDestination


class LibraryCollectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["COLLECTION"]
    new_collection_name: LibraryName
    parent: CollectionParent


class LibraryMeshOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["MESH"]
    new_mesh_name: LibraryName
    new_object_name: LibraryName
    collection: LibraryCollectionDestination

    @model_validator(mode="after")
    def distinct_names(self) -> LibraryMeshOutput:
        if self.new_mesh_name == self.new_object_name:
            raise ValueError("new_mesh_name and new_object_name must be distinct")
        return self


LibraryOutput = Annotated[
    LibraryObjectOutput | LibraryCollectionOutput | LibraryMeshOutput,
    Field(discriminator="type"),
]


def library_entry_identity(file_sha256: str, kind: LibraryKind, name: str) -> str:
    payload = f"{file_sha256}:{kind}:{name}".encode()
    return hashlib.sha256(payload).hexdigest()


def inspect_local_library_file(path_value: str) -> dict[str, object]:
    """Return immutable file evidence without asking Blender to load any data-block."""

    path = Path(path_value)
    if not path.is_absolute() or path.suffix.lower() != ".blend":
        raise bridge_error(
            ErrorKind.VALIDATION,
            "LIBRARY_PATH_INVALID",
            "Library path must be an absolute .blend path",
        )
    normalized = Path(os.path.realpath(path))
    if not normalized.exists() or not normalized.is_file():
        raise bridge_error(
            ErrorKind.NOT_FOUND,
            "LIBRARY_NOT_FOUND",
            f"Library file does not exist: {normalized}",
        )
    try:
        stat_before = normalized.stat()
        hasher = hashlib.sha256()
        header = b""
        with normalized.open("rb") as stream:
            header = stream.read(12)
            hasher.update(header)
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
        stat_after = normalized.stat()
    except OSError as exc:
        raise bridge_error(
            ErrorKind.UNAVAILABLE,
            "LIBRARY_INSPECTION_FAILED",
            f"Library file could not be read: {normalized}",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        ) from exc
    if (
        stat_before.st_size != stat_after.st_size
        or stat_before.st_mtime_ns != stat_after.st_mtime_ns
    ):
        raise bridge_error(
            ErrorKind.CONFLICT,
            "LIBRARY_FILE_CHANGED",
            "Library file changed while it was being inspected",
            retryable=True,
        )
    if len(header) != 12 or not header.startswith(b"BLENDER"):
        raise bridge_error(
            ErrorKind.VALIDATION,
            "LIBRARY_FORMAT_UNSUPPORTED",
            "Library file does not have a supported Blender header",
        )
    pointer = chr(header[7])
    endian = chr(header[8])
    version_raw = header[9:12].decode("ascii", errors="replace")
    return {
        "path": str(normalized),
        "file_sha256": hasher.hexdigest(),
        "size_bytes": int(stat_after.st_size),
        "modified_ns": int(stat_after.st_mtime_ns),
        "blend_header": {
            "pointer_size": {"_": 4, "-": 8}.get(pointer),
            "endianness": {"v": "LITTLE", "V": "BIG"}.get(endian, "UNKNOWN"),
            "version": version_raw,
        },
    }


__all__ = [
    "LibraryEntry",
    "LibraryKind",
    "LibraryOutput",
    "LibrarySource",
    "inspect_local_library_file",
    "library_entry_identity",
]
