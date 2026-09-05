import asyncio
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES
from blender_research_mcp.errors import TransportError, transport_error
from blender_research_mcp.framing import encode_frame, read_frame
from blender_research_mcp.protocol import CapabilityVersions, HandshakeResult


def test_client_handshake_ping_and_read_only_reconnect(tmp_path) -> None:
    async def scenario() -> tuple[dict[str, object], int]:
        connection_count = 0

        async def handler(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal connection_count
            connection_count += 1
            connection_number = connection_count
            hello = await read_frame(reader, max_bytes=MAX_REQUEST_BYTES)
            hello_response = {
                "protocol": 1,
                "request_id": hello["request_id"],
                "ok": True,
                "scene_generation": 0,
                "result": {
                    "protocol": 1,
                    "instance_id": "instance-1",
                    "blender_version": "4.2.23",
                    "addon_version": "0.5.1",
                    "capabilities": ["connection.ping"],
                    "capability_versions": {
                        "transport": 1,
                        "context": 1,
                        "viewport_capture": 3,
                        "viewport_raycast": 1,
                        "geometry_inspection": 1,
                        "lookdev_inspection": 1,
                        "transactions": 2,
                        "object_transform_scale": 1,
                        "object_visibility": 1,
                        "modifier_state": 1,
                        "shape_key_value": 1,
                        "material_input": 1,
                        "mesh_selection": 1,
                        "mesh_surface_query": 1,
                        "mesh_deformation": 1,
                        "mesh_validation": 1,
                    },
                },
            }
            writer.write(encode_frame(hello_response, max_bytes=MAX_RESPONSE_BYTES))
            await writer.drain()

            ping = await read_frame(reader, max_bytes=MAX_REQUEST_BYTES)
            if connection_number == 1:
                writer.close()
                await writer.wait_closed()
                return
            ping_response = {
                "protocol": 1,
                "request_id": ping["request_id"],
                "ok": True,
                "scene_generation": 4,
                "result": {"heartbeat": 9, "label": "中文"},
            }
            frame = encode_frame(ping_response, max_bytes=MAX_RESPONSE_BYTES)
            writer.write(frame[:5])
            await writer.drain()
            writer.write(frame[5:])
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        manifest_path = tmp_path / "session.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "protocol": 1,
                    "host": "127.0.0.1",
                    "port": port,
                    "pid": os.getpid(),
                    "instance_id": "instance-1",
                    "session_token": "t" * 43,
                    "addon_version": "0.5.1",
                    "created_at": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        client = BridgeClient(port=port, session_file=manifest_path)
        try:
            result = await client.call("connection.ping", read_only=True)
        finally:
            await client.close()
            server.close()
            await server.wait_closed()
        return result, connection_count

    result, connection_count = asyncio.run(scenario())
    assert result == {"heartbeat": 9, "label": "中文", "scene_generation": 4}
    assert connection_count == 2


def test_client_rejects_addon_without_offscreen_capability(tmp_path) -> None:
    async def scenario() -> None:
        async def handler(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            hello = await read_frame(reader, max_bytes=MAX_REQUEST_BYTES)
            response = {
                "protocol": 1,
                "request_id": hello["request_id"],
                "ok": True,
                "scene_generation": 0,
                "result": {
                    "protocol": 1,
                    "instance_id": "instance-old",
                    "blender_version": "4.2.23",
                    "addon_version": "0.2.0",
                    "capabilities": ["viewport.capture"],
                },
            }
            writer.write(encode_frame(response, max_bytes=MAX_RESPONSE_BYTES))
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        manifest_path = tmp_path / "session-old.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "protocol": 1,
                    "host": "127.0.0.1",
                    "port": port,
                    "pid": os.getpid(),
                    "instance_id": "instance-old",
                    "session_token": "t" * 43,
                    "addon_version": "0.2.0",
                    "created_at": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        client = BridgeClient(port=port, session_file=manifest_path)
        try:
            with pytest.raises(TransportError) as mismatch:
                await client.connect()
            assert mismatch.value.error.code == "CAPABILITY_MISMATCH"
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_client_normalizes_expected_reload_reset_during_handshake(tmp_path) -> None:
    async def scenario() -> None:
        async def handler(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            await read_frame(reader, max_bytes=MAX_REQUEST_BYTES)
            writer.transport.abort()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        manifest_path = tmp_path / "session-reload.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "protocol": 1,
                    "host": "127.0.0.1",
                    "port": port,
                    "pid": os.getpid(),
                    "instance_id": "instance-reload",
                    "session_token": "t" * 43,
                    "addon_version": "0.17.0",
                    "created_at": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        client = BridgeClient(port=port, session_file=manifest_path)
        try:
            with pytest.raises(TransportError) as reset:
                await client.connect()
            assert reset.value.error.code == "CONNECT_FAILED"
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_lifecycle_capabilities_are_enforced_per_tool() -> None:
    client = BridgeClient()
    client._handshake = HandshakeResult(
        protocol=1,
        instance_id="legacy-0.6",
        blender_version="4.2.23",
        addon_version="0.6.0",
        capabilities=["connection.ping"],
        capability_versions=CapabilityVersions(
            transport=1,
            context=1,
            viewport_capture=3,
            viewport_raycast=1,
            geometry_inspection=1,
            lookdev_inspection=1,
            transactions=2,
            object_transform_scale=1,
            object_visibility=1,
            modifier_state=1,
            shape_key_value=1,
            material_input=1,
        ),
    )

    with pytest.raises(TransportError) as mismatch:
        client.require_capability("project_lifecycle", 1)
    assert mismatch.value.error.code == "CAPABILITY_MISMATCH"
    assert mismatch.value.error.details == {
        "capabilities": {"project_lifecycle": {"required": 1, "actual": 0}}
    }


def test_close_does_not_await_proactor_transport_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ResetWriter:
        waited = False

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            self.waited = True
            raise ConnectionResetError(64, "connection reset during project reload")

    async def scenario() -> None:
        client = BridgeClient()
        writer = ResetWriter()
        client._writer = writer  # type: ignore[assignment]
        await client.close()
        assert client._writer is None
        assert writer.waited is False

    monkeypatch.setattr("blender_research_mcp.client.sys.platform", "win32")
    asyncio.run(scenario())


def test_request_timeout_is_reported_without_retrying_as_connection_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def connect(client: BridgeClient) -> None:
        client._manifest = SimpleNamespace(session_token="t" * 43)  # type: ignore[assignment]

    async def round_trip(_client: BridgeClient, _request) -> None:
        nonlocal attempts
        attempts += 1
        raise transport_error("REQUEST_TIMEOUT", "bounded read timed out")

    async def scenario() -> None:
        client = BridgeClient()
        monkeypatch.setattr(client, "_connect_locked", lambda: connect(client))
        monkeypatch.setattr(
            client,
            "_round_trip_locked",
            lambda request: round_trip(client, request),
        )
        with pytest.raises(TransportError) as timeout:
            await client.call("mesh.uv.inspect", read_only=True)
        assert timeout.value.error.code == "REQUEST_TIMEOUT"
        assert attempts == 1

    asyncio.run(scenario())
