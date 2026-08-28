import json
import os
from datetime import UTC, datetime

import pytest

from blender_research_mcp.errors import TransportError
from blender_research_mcp.session import load_manifest


def write_manifest(path, *, port: int, pid: int | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "protocol": 1,
                "host": "127.0.0.1",
                "port": port,
                "pid": pid or os.getpid(),
                "instance_id": "instance-1",
                "session_token": "s" * 43,
                "addon_version": "0.2.0",
                "created_at": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )


def test_load_manifest_validates_endpoint_and_process(tmp_path) -> None:
    path = tmp_path / "session.json"
    write_manifest(path, port=9877)
    manifest = load_manifest(9877, path)
    assert manifest.pid == os.getpid()

    with pytest.raises(TransportError) as mismatch:
        load_manifest(9878, path)
    assert mismatch.value.error.code == "SESSION_MISMATCH"


def test_load_manifest_rejects_invalid_json(tmp_path) -> None:
    path = tmp_path / "session.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(TransportError) as invalid:
        load_manifest(9877, path)
    assert invalid.value.error.code == "SESSION_INVALID"
