from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

import pytest

from metermaid.schema_advisor import (
    AdvisorConfig,
    AdvisorError,
    SchemaGroup,
    group_records,
)
from metermaid.schema_advisor_client import Advice, advise_groups

CANARY = "PRIVATE_MODEL_RATIONALE_CANARY"


@dataclass
class ServerState:
    body: bytes = b'{"response":"{\\"category\\":\\"possible-usage-shape\\"}"}'
    status: int = 200
    delay: float = 0
    requests: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)


@pytest.fixture
def local_server() -> Iterator[tuple[ServerState, AdvisorConfig]]:
    state = ServerState()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers["Content-Length"])
            payload = cast(dict[str, object], json.loads(self.rfile.read(size)))
            state.requests.append((self.command, self.path, payload))
            time.sleep(state.delay)
            self.send_response(state.status)
            self.send_header("Content-Length", str(len(state.body)))
            if state.status == 302:
                self.send_header("Location", "/redirect-canary")
            self.end_headers()
            try:
                self.wfile.write(state.body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            state,
            AdvisorConfig(
                endpoint=f"http://localhost:{server.server_port}/api/generate",
                model="configured-local-model",
                timeout_seconds=1,
            ),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def groups() -> tuple[SchemaGroup, ...]:
    return group_records(
        [
            ("codex", "unknown", {"type": CANARY, "message": CANARY}),
            ("codex", "unknown", {"type": "different", "message": "different"}),
        ]
    )


def assert_failed(result: tuple[Advice, ...]) -> None:
    assert len(result) == 1
    assert result[0].failed
    assert result[0].category == "needs-human-review"
    assert CANARY not in repr(result)


def test_aggregated_request_and_proxy_bypass(
    local_server: tuple[ServerState, AdvisorConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, config = local_server
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(name, "http://192.0.2.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    original = socket.getaddrinfo

    def numeric_only(host: str, *args: object, **kwargs: object) -> object:
        assert host == "127.0.0.1"
        return original(host, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "getaddrinfo", numeric_only)
    grouped = groups()
    assert len(grouped) == 1
    result = advise_groups(config, grouped)
    assert result == (Advice(grouped[0], "possible-usage-shape", False),)
    assert len(state.requests) == 1
    method, path, payload = state.requests[0]
    assert (method, path) == ("POST", "/api/generate")
    assert payload["model"] == "configured-local-model"
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert '"count":2' in str(payload["prompt"])
    assert CANARY not in json.dumps(payload)


@pytest.mark.parametrize(
    "nested",
    [
        "not json " + CANARY,
        '{"category":"unknown"}',
        '{"category":"possible-usage-shape","rationale":"' + CANARY + '"}',
        '{"category":"expected-envelope","category":"possible-usage-shape"}',
        '{"category":null}',
        '{"category":NaN}',
        "[]",
    ],
)
def test_rejected_nested_output(
    local_server: tuple[ServerState, AdvisorConfig], nested: str
) -> None:
    state, config = local_server
    state.body = json.dumps({"response": nested}).encode()
    assert_failed(advise_groups(config, groups()))
    assert len(state.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"invalid",
        b'{"response":4}',
        b'{"response":null}',
        b'{"response":"{}","response":"{}"}',
        b'{"response":NaN}',
        b"x" * 65_537,
    ],
)
def test_rejected_transport_output(
    local_server: tuple[ServerState, AdvisorConfig], body: bytes
) -> None:
    state, config = local_server
    state.body = body
    assert_failed(advise_groups(config, groups()))
    assert len(state.requests) == 1


@pytest.mark.parametrize("status", [302, 401, 500])
def test_http_failure_never_redirects_or_retries(
    local_server: tuple[ServerState, AdvisorConfig], status: int
) -> None:
    state, config = local_server
    state.status = status
    state.body = CANARY.encode()
    assert_failed(advise_groups(config, groups()))
    assert len(state.requests) == 1


def test_read_timeout_is_safe(local_server: tuple[ServerState, AdvisorConfig]) -> None:
    state, config = local_server
    state.delay = 1.2
    state.body = CANARY.encode()
    assert_failed(advise_groups(config, groups()))
    assert len(state.requests) == 1


def test_valid_human_review_is_not_failure(
    local_server: tuple[ServerState, AdvisorConfig],
) -> None:
    state, config = local_server
    state.body = json.dumps({"response": '{"category":"needs-human-review"}'}).encode()
    result = advise_groups(config, groups())
    assert result[0].category == "needs-human-review"
    assert not result[0].failed


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://192.0.2.1/api/generate",
        "http://example.com/api/generate",
        "https://127.0.0.1/api/generate",
        "http://127.0.0.1/other",
        "http://127.0.0.1/api/generate?secret=" + CANARY,
        "http://user:password@localhost/api/generate",
    ],
)
def test_manual_remote_config_rejected_before_connection(
    endpoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid endpoint opened a connection")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    with pytest.raises(AdvisorError) as error:
        advise_groups(AdvisorConfig(endpoint, "local", 1), groups())
    assert CANARY not in str(error.value)
    assert endpoint not in str(error.value)


@pytest.mark.parametrize(
    "category",
    [
        "expected-envelope",
        "non-usage-message",
        "possible-usage-shape",
        "needs-human-review",
    ],
)
def test_every_category_is_accepted(
    local_server: tuple[ServerState, AdvisorConfig], category: str
) -> None:
    state, config = local_server
    state.body = json.dumps({"response": json.dumps({"category": category})}).encode()
    result = advise_groups(config, groups())
    assert result[0].category == category
    assert not result[0].failed
    assert len(state.requests) == 1


def test_ipv6_loopback() -> None:
    class IPv6Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b'{"response":"{\\"category\\":\\"expected-envelope\\"}"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    try:
        server = IPv6Server(("::1", 0), Handler)
    except OSError:
        pytest.skip("IPv6 loopback is unavailable")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = AdvisorConfig(
            f"http://[::1]:{server.server_port}/api/generate", "local", 1
        )
        result = advise_groups(config, groups())
        assert result[0].category == "expected-envelope"
        assert not result[0].failed
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_connection_refused_is_safe() -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        config = AdvisorConfig(
            f"http://127.0.0.1:{reserved.getsockname()[1]}/api/generate", "local", 1
        )
        assert_failed(advise_groups(config, groups()))
