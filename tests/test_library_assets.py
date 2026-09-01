from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.errors import BridgeError
from blender_research_mcp.library_assets import (
    LibraryOutput,
    LibrarySource,
    inspect_local_library_file,
    library_entry_identity,
)


def _write_blend_header(path: Path, payload: bytes = b"fixture") -> bytes:
    content = b"BLENDER-v420" + payload
    path.write_bytes(content)
    return content


def test_library_file_evidence_is_streamed_and_header_bound(tmp_path: Path) -> None:
    path = tmp_path / "template.blend"
    content = _write_blend_header(path)

    result = inspect_local_library_file(str(path))

    assert result["path"] == str(path.resolve())
    assert result["size_bytes"] == len(content)
    assert result["file_sha256"] == hashlib.sha256(content).hexdigest()
    assert result["blend_header"] == {
        "pointer_size": 8,
        "endianness": "LITTLE",
        "version": "420",
    }


@pytest.mark.parametrize("name", ["relative.blend", "absolute.txt"])
def test_library_file_evidence_rejects_invalid_paths(tmp_path: Path, name: str) -> None:
    path = Path(name) if name.startswith("relative") else tmp_path / name
    if path.is_absolute():
        path.write_bytes(b"not a blend")

    with pytest.raises(BridgeError) as caught:
        inspect_local_library_file(str(path))

    assert caught.value.error.code == "LIBRARY_PATH_INVALID"


def test_library_file_evidence_rejects_non_blender_header(tmp_path: Path) -> None:
    path = tmp_path / "broken.blend"
    path.write_bytes(b"not-a-blender-file")

    with pytest.raises(BridgeError) as caught:
        inspect_local_library_file(str(path))

    assert caught.value.error.code == "LIBRARY_FORMAT_UNSUPPORTED"


def test_library_entry_identity_is_type_and_name_bound() -> None:
    digest = "a" * 64

    assert library_entry_identity(digest, "OBJECT", "Head") == hashlib.sha256(
        f"{digest}:OBJECT:Head".encode()
    ).hexdigest()
    assert library_entry_identity(digest, "OBJECT", "Head") != library_entry_identity(
        digest, "MESH", "Head"
    )


def test_library_append_schemas_are_closed_and_discriminated() -> None:
    source = LibrarySource(
        path=str(Path.cwd() / "template.blend"),
        expected_file_sha256="a" * 64,
        expected_size_bytes=12,
    )
    assert source.expected_size_bytes == 12

    output = TypeAdapter(LibraryOutput).validate_python(
        {
            "type": "MESH",
            "new_mesh_name": "HeadTemplateMesh",
            "new_object_name": "HeadTemplate",
            "collection": {
                "collection_name": "Templates",
                "expected_collection_identity": "collection:1",
                "expected_collection_structure_fingerprint": "b" * 64,
            },
        }
    )
    assert output.type == "MESH"
    with pytest.raises(ValidationError):
        TypeAdapter(LibraryOutput).validate_python(
            {
                "type": "MESH",
                "new_mesh_name": "Same",
                "new_object_name": "Same",
                "collection": {
                    "collection_name": "Templates",
                    "expected_collection_identity": "collection:1",
                    "expected_collection_structure_fingerprint": "b" * 64,
                },
            }
        )
    with pytest.raises(ValidationError):
        LibrarySource.model_validate(
            {
                "path": str(Path.cwd() / "template.blend"),
                "expected_file_sha256": "a" * 64,
                "expected_size_bytes": 12,
                "unexpected": True,
            }
        )
