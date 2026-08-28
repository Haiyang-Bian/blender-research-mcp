"""Persistent authenticated client for the Blender add-on listener."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from blender_research_mcp.constants import (
    DEFAULT_DEADLINE_MS,
    DEFAULT_PORT,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    PROTOCOL_VERSION,
)
from blender_research_mcp.errors import BridgeError, TransportError, transport_error
from blender_research_mcp.framing import FramingError, encode_frame, read_frame
from blender_research_mcp.protocol import HandshakeResult, RequestEnvelope, ResponseEnvelope
from blender_research_mcp.session import SessionManifest, load_manifest


class BridgeClient:
    """Serialize commands over one reconnectable loopback connection."""

    def __init__(self, *, port: int = DEFAULT_PORT, session_file: Path | None = None) -> None:
        self.port = port
        self.session_file = session_file
        self._manifest: SessionManifest | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._handshake: HandshakeResult | None = None

    @property
    def handshake(self) -> HandshakeResult | None:
        return self._handshake

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        self._reader = None
        self._manifest = None
        self._handshake = None
        if writer is not None:
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def connect(self) -> HandshakeResult:
        async with self._lock:
            return await self._connect_locked()

    async def _connect_locked(self) -> HandshakeResult:
        if self._writer is not None and not self._writer.is_closing() and self._handshake:
            return self._handshake
        await self.close()
        manifest = load_manifest(self.port, self.session_file)
        try:
            reader, writer = await asyncio.open_connection(manifest.host, manifest.port)
        except (ConnectionError, OSError) as exc:
            raise transport_error(
                "CONNECT_FAILED",
                f"Could not connect to Blender at {manifest.host}:{manifest.port}",
            ) from exc
        self._manifest = manifest
        self._reader = reader
        self._writer = writer
        try:
            response = await self._round_trip_locked(
                RequestEnvelope(
                    request_id=uuid4(),
                    session_token=manifest.session_token,
                    command="connection.hello",
                    params={
                        "server_version": "0.2.0",
                        "protocol_min": PROTOCOL_VERSION,
                        "protocol_max": PROTOCOL_VERSION,
                        "expected_instance_id": manifest.instance_id,
                    },
                )
            )
            result = HandshakeResult.model_validate(response.result or {})
        except Exception:
            await self.close()
            raise
        if result.protocol != PROTOCOL_VERSION or result.instance_id != manifest.instance_id:
            await self.close()
            raise transport_error(
                "HANDSHAKE_MISMATCH",
                "Blender handshake did not match the discovered session",
                retryable=False,
            )
        self._handshake = result
        return result

    async def call(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        deadline_ms: int = DEFAULT_DEADLINE_MS,
        expected_scene_generation: int | None = None,
        idempotency_key: str | None = None,
        read_only: bool,
    ) -> dict[str, Any]:
        may_retry = read_only or idempotency_key is not None
        last_error: Exception | None = None
        for attempt in range(2 if may_retry else 1):
            try:
                async with self._lock:
                    await self._connect_locked()
                    assert self._manifest is not None
                    response = await self._round_trip_locked(
                        RequestEnvelope(
                            request_id=uuid4(),
                            session_token=self._manifest.session_token,
                            command=command,
                            params=params or {},
                            deadline_ms=deadline_ms,
                            expected_scene_generation=expected_scene_generation,
                            idempotency_key=idempotency_key,
                        )
                    )
                    result = dict(response.result or {})
                    result.setdefault("scene_generation", response.scene_generation)
                    return result
            except TransportError as exc:
                last_error = exc
                await self.close()
                if attempt == 0 and may_retry:
                    continue
            except BridgeError:
                raise
            except (asyncio.IncompleteReadError, ConnectionError, OSError, FramingError) as exc:
                last_error = exc
                await self.close()
                if attempt == 0 and may_retry:
                    continue
        raise transport_error("CONNECTION_LOST", "Connection to Blender was lost") from last_error

    async def _round_trip_locked(self, request: RequestEnvelope) -> ResponseEnvelope:
        if self._reader is None or self._writer is None:
            raise transport_error("NOT_CONNECTED", "Blender connection is not established")
        try:
            self._writer.write(
                encode_frame(
                    request.model_dump(mode="json", exclude_none=True),
                    max_bytes=MAX_REQUEST_BYTES,
                )
            )
            await self._writer.drain()
            raw = await asyncio.wait_for(
                read_frame(self._reader, max_bytes=MAX_RESPONSE_BYTES),
                timeout=request.deadline_ms / 1000,
            )
        except TimeoutError as exc:
            raise transport_error(
                "REQUEST_TIMEOUT",
                f"Blender did not respond within {request.deadline_ms} ms",
            ) from exc
        response = ResponseEnvelope.model_validate(raw)
        if response.protocol != PROTOCOL_VERSION:
            raise transport_error(
                "PROTOCOL_MISMATCH",
                f"Blender responded with unsupported protocol {response.protocol}",
                retryable=False,
            )
        if response.request_id != UUID(str(request.request_id)):
            raise transport_error(
                "REQUEST_ID_MISMATCH",
                "Blender response request_id did not match the request",
                retryable=False,
            )
        if not response.ok:
            assert response.error is not None
            raise BridgeError(response.error)
        return response
