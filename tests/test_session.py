import json
import os
from datetime import UTC, datetime

import pytest

from blender_research_mcp import session
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
                "addon_version": "0.3.0",
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


def test_discovers_microsoft_store_blender_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(session.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    package_manifest = (
        tmp_path
        / "Packages"
        / "BlenderFoundation.Blender4.2LTS_publisher"
        / "LocalCache"
        / "Local"
        / "blender-research-mcp"
        / "runtime"
        / "session-9877.json"
    )
    package_manifest.parent.mkdir(parents=True)
    write_manifest(package_manifest, port=9877)

    manifest = load_manifest(9877)

    assert manifest.instance_id == "instance-1"


def test_ignores_stale_primary_when_store_session_is_live(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(session.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    primary = tmp_path / "blender-research-mcp" / "runtime" / "session-9877.json"
    primary.parent.mkdir(parents=True)
    write_manifest(primary, port=9877, pid=111)
    package_manifest = (
        tmp_path
        / "Packages"
        / "BlenderFoundation.Blender4.2LTS_publisher"
        / "LocalCache"
        / "Local"
        / "blender-research-mcp"
        / "runtime"
        / "session-9877.json"
    )
    package_manifest.parent.mkdir(parents=True)
    write_manifest(package_manifest, port=9877)
    monkeypatch.setattr(session, "pid_exists", lambda pid: pid == os.getpid())

    manifest = load_manifest(9877)

    assert manifest.pid == os.getpid()


def test_rejects_multiple_live_store_blender_sessions(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(session.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for index in range(2):
        package_manifest = (
            tmp_path
            / "Packages"
            / f"BlenderFoundation.Blender4.{index}LTS_publisher"
            / "LocalCache"
            / "Local"
            / "blender-research-mcp"
            / "runtime"
            / "session-9877.json"
        )
        package_manifest.parent.mkdir(parents=True)
        write_manifest(package_manifest, port=9877)
        payload = json.loads(package_manifest.read_text(encoding="utf-8"))
        payload["instance_id"] = f"instance-{index}"
        package_manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TransportError) as conflict:
        load_manifest(9877)

    assert conflict.value.error.code == "SESSION_CONFLICT"
