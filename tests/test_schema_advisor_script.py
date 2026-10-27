"""Consumer-level privacy and read-only contracts for the developer advisor."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from metermaid.domain import ParseOutcome
from metermaid.schema_advisor_runner import main
from metermaid.store import EventStore

_CANARY = "SOURCE_CANARY_private_project_model_id"
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "schema_advisor.py"
_FIXTURE = Path(__file__).parent / "fixtures" / "m3" / "omp-message.jsonl"


@pytest.fixture
def endpoint() -> Iterator[tuple[str, list[bytes]]]:
    """Serve constrained advice while capturing the actual outbound boundary."""
    requests: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(self.rfile.read(int(self.headers["Content-Length"])))
            payload = json.dumps(
                {"response": '{"category":"possible-usage-shape"}'}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/api/generate", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _config(path: Path, endpoint: str) -> None:
    """Write an explicit fixture opt-in rather than a user's default config."""
    path.write_text(
        "[schema_advisor]\nenabled = true\n"
        f'endpoint = "{endpoint}"\nmodel = "fixture-local-model"\ntimeout_seconds = 2\n'
    )


@pytest.mark.parametrize(
    "content",
    [
        None,
        "[schema_advisor]\nenabled = false\n",
        '[schema_advisor]\nenabled = true\nendpoint = "https://example.com/api/generate"\n'
        'model = "fixture-model"\ntimeout_seconds = 2\n',
        "[schema_advisor\n",
    ],
)
def test_invalid_opt_in_fails_before_source_discovery(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: str | None,
) -> None:
    config = tmp_path / f"{_CANARY}.toml"
    if content is not None:
        config.write_text(content)

    def forbidden_discovery() -> None:
        raise AssertionError("Discovery occurred before opt-in validation")

    monkeypatch.setattr(
        "metermaid.schema_advisor_runner.documented_source_roots", forbidden_discovery
    )

    assert main(["--config", str(config)]) == 1
    output = capsys.readouterr()
    assert "needs-human-review" in output.err
    assert _CANARY not in output.out + output.err
    assert str(tmp_path) not in output.out + output.err


def test_source_script_groups_only_unsupported_complete_records_without_writes(
    tmp_path: Path, endpoint: tuple[str, list[bytes]]
) -> None:
    url, requests = endpoint
    home = tmp_path / "home"
    sources = home / ".omp" / "agent" / "sessions"
    sources.mkdir(parents=True)
    record: dict[str, object] = {
        "type": _CANARY,
        "timestamp": _CANARY,
        "message": {
            "model": _CANARY,
            "role": _CANARY,
            "content": [{"text": _CANARY, "toolArguments": {_CANARY: _CANARY}}],
        },
        _CANARY: {"result": _CANARY},
    }
    second = {**record, "type": _CANARY + "_second"}
    source = sources / f"{_CANARY}.jsonl"
    source.write_bytes(
        _FIXTURE.read_bytes()
        + b"\n{malformed\n"
        + json.dumps(record).encode()
        + b"\n"
        + json.dumps(second).encode()
        + b"\n"
        + json.dumps(record).encode()
    )
    config = tmp_path / "advisor.toml"
    _config(config, url)
    state = home / ".metermaid"
    state.mkdir()
    store = EventStore(state / "metermaid.sqlite3")
    store.initialize()
    store.commit_ingest(
        [],
        [ParseOutcome(agent="omp", discriminator="message", kind="unsupported")],
        None,
    )
    diagnostics = store.diagnostics()
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    environment = dict(os.environ, HOME=str(home))
    environment.pop("CODEX_HOME", None)
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "empty-config")

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--config", str(config)],
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert len(requests) == 1
    rows = result.stdout.splitlines()
    assert rows[1].split("\t")[3:] == ["2", "possible-usage-shape"]
    assert _CANARY not in result.stdout + result.stderr
    assert str(home) not in result.stdout + result.stderr
    assert _CANARY.encode() not in requests[0]
    assert str(home).encode() not in requests[0]
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    assert store.diagnostics() == diagnostics
