import contextlib
import importlib.util
import queue
import socket
import sys
import threading
import time
import types
from pathlib import Path


def load_addon_module(module_name: str):
    source = Path(__file__).parents[1] / "blender_addon" / "blender_research_mcp_addon"
    package_name = "addon_runtime_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(source)]
    sys.modules[package_name] = package
    for name in ("wire", module_name):
        qualified = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(qualified, source / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{package_name}.{module_name}"]


def test_cancelled_request_is_never_dispatched() -> None:
    runtime_module = load_addon_module("runtime")
    runtime = runtime_module.ListenerRuntime()
    pending = runtime_module.PendingRequest(
        request={"request_id": "cancelled"},
        event=threading.Event(),
        cancelled=True,
    )
    runtime._requests = queue.Queue()
    runtime._requests.put(pending)
    dispatched = False

    def dispatcher(_request):
        nonlocal dispatched
        dispatched = True
        return {}

    runtime.poll(dispatcher, lambda: None)

    assert dispatched is False
    assert pending.event.is_set()
def test_listener_authenticates_before_dispatch_and_removes_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_module = load_addon_module("runtime")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
    reservation.close()
    runtime = runtime_module.ListenerRuntime(port=port)
    runtime.start()
    deadline = time.monotonic() + 3
    while runtime.status == "starting" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runtime.status == "listening"
    assert runtime.manifest_path.exists()

    client = socket.create_connection(("127.0.0.1", port), timeout=1)
    client.settimeout(1)
    request = {
        "protocol": 1,
        "request_id": "00000000-0000-0000-0000-000000000001",
        "session_token": runtime.session_token,
        "command": "connection.ping",
        "params": {},
        "deadline_ms": 1000,
    }
    client.sendall(runtime_module.encode_frame(request))
    dispatched = False

    def dispatcher(received):
        nonlocal dispatched
        dispatched = True
        return {
            "protocol": 1,
            "request_id": received["request_id"],
            "ok": True,
            "scene_generation": 3,
            "result": {"heartbeat": 7},
        }

    response = b""
    deadline = time.monotonic() + 2
    while not response and time.monotonic() < deadline:
        runtime.poll(dispatcher, lambda: None)
        with contextlib.suppress(TimeoutError):
            response = client.recv(4096)
    decoder = runtime_module.FrameDecoder()
    assert decoder.feed(response)[0]["result"] == {"heartbeat": 7}
    assert dispatched is True
    assert runtime.connected is True

    client.close()
    runtime.stop()
    assert runtime.manifest_path.exists() is False
