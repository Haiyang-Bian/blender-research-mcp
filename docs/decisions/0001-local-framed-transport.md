# 0001 — Authenticated framed loopback transport

- Status: accepted
- Date: 2026-08-28

## Context

The Blender add-on and external MCP process need a local transport that does not
run socket I/O on Blender's main thread and does not expose arbitrary Python.
Messages may be fragmented or coalesced by TCP, and viewport images are larger
than ordinary command responses.

## Decision

- Bind the add-on listener to `127.0.0.1:9877` by default.
- Prefix every UTF-8 JSON object with a four-byte unsigned big-endian length.
- Limit requests to 1 MiB and responses to 32 MiB.
- Use protocol version 1 envelopes with request IDs, deadlines, structured
  errors, scene generations, and mutation idempotency keys.
- Generate a random token and instance ID for each add-on listener session.
- Publish them atomically in the current user's local runtime directory; reject
  stale PID, endpoint, instance, token, or protocol data during handshake.
- Allow at most one active Blender listener per port.
- Never retry a mutation unless the caller supplied an idempotency key.

## Alternatives

- Newline-delimited JSON was rejected because binary-sized payloads and partial
  reads make line handling fragile.
- Treating one `recv` as one message was rejected because TCP has no message
  boundaries.
- HTTP or an external broker was rejected for the first milestone because it
  adds dependencies without improving the loopback-only authority boundary.

## Consequences

The add-on must implement a small framed socket layer using only the Python
standard library. Screenshot responses must remain bounded. Protocol changes
require explicit negotiation and a new decision record.
