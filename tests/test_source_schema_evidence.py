"""Fixture contracts that close the M3 source-schema evidence gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from metermaid.domain import NormalizedEvent
from metermaid.state import event_identifier, opaque_identifier

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "m3"
_SECRET = b"fixture-secret-for-schema-evidence"


def _fixture(name: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads((FIXTURE_ROOT / name).read_text()))


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    assert isinstance(value, str)
    return value


def _integer(value: object) -> int:
    assert isinstance(value, int)
    return value


def _number(value: object) -> float:
    assert isinstance(value, int | float)
    return float(value)


def _event(
    agent: str,
    discriminator: str,
    session: str,
    model: str,
    role: str,
    tokens_in: int,
    tokens_out: int,
    cache_read: int | None,
    cache_write: int | None,
    provider_cost_usd: float | None,
) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_identifier(_SECRET, agent, discriminator, session),
        agent=agent,
        source_session_id=opaque_identifier(_SECRET, "session", session),
        project_key=opaque_identifier(_SECRET, "project", "fixture-project"),
        occurred_at=datetime(2026, 8, 16, tzinfo=UTC),
        record_kind="usage",
        provenance=f"{agent}.{discriminator}",
        role=role,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
        cache_write=cache_write,
        provider_cost_usd=provider_cost_usd,
    )


def test_claude_fixture_maps_usage_shape() -> None:
    record = _fixture("claude-assistant.jsonl")
    message = _object(record["message"])
    usage = _object(message["usage"])

    event = _event(
        "claude-code",
        _string(record["type"]),
        _string(record["sessionId"]),
        _string(message["model"]),
        _string(message["role"]),
        _integer(usage["input_tokens"]),
        _integer(usage["output_tokens"]),
        _integer(usage["cache_read_input_tokens"]),
        _integer(usage["cache_creation_input_tokens"]),
        None,
    )

    assert event.tokens_in == 100


def test_codex_fixture_maps_usage_shape() -> None:
    record = _fixture("codex-token-count.jsonl")
    payload = _object(record["payload"])
    info = _object(payload["info"])
    usage = _object(info["total_token_usage"])

    event = _event(
        "codex",
        _string(payload["type"]),
        "fixture-session",
        "fixture-model",
        "assistant",
        _integer(usage["input_tokens"]),
        _integer(usage["output_tokens"]),
        _integer(usage["cached_input_tokens"]),
        None,
        None,
    )

    assert event.tokens_out == 20


def test_pi_fixture_maps_usage_shape() -> None:
    record = _fixture("pi-message.jsonl")
    message = _object(record["message"])
    usage = _object(message["usage"])
    cost = _object(usage["cost"])

    event = _event(
        "pi",
        _string(record["type"]),
        "fixture-session",
        _string(message["model"]),
        _string(message["role"]),
        _integer(usage["input"]),
        _integer(usage["output"]),
        _integer(usage["cacheRead"]),
        _integer(usage["cacheWrite"]),
        _number(cost["total"]),
    )

    assert event.provider_cost_usd == 0.032


def test_omp_fixture_maps_usage_shape() -> None:
    record = _fixture("omp-message.jsonl")
    message = _object(record["message"])
    usage = _object(message["usage"])
    cost = _object(usage["cost"])

    event = _event(
        "omp",
        _string(record["type"]),
        "fixture-session",
        _string(message["model"]),
        _string(message["role"]),
        _integer(usage["input"]),
        _integer(usage["output"]),
        _integer(usage["cacheRead"]),
        _integer(usage["cacheWrite"]),
        _number(cost["total"]),
    )

    assert _string(message["toolName"]) == "read"
    assert event.provider_cost_usd == 0.032
