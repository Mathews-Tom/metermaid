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


def _record_timestamp(record: dict[str, object]) -> datetime:
    """Derive ``occurred_at`` from the record's top-level ``timestamp``.

    Every reviewed fixture carries a top-level ``timestamp``; only Pi and
    OMP additionally carry a nested ``message.timestamp`` epoch-ms value.
    The top-level field is authoritative because it is the only one all
    four sources share, keeping the derivation rule uniform across agents.
    """
    return datetime.fromisoformat(_string(record["timestamp"]).replace("Z", "+00:00"))


def _event(
    agent: str,
    discriminator: str,
    session: str,
    occurred_at: datetime,
    tokens_in: int,
    tokens_out: int,
    cache_read: int | None,
    cache_write: int | None,
    provider_cost_usd: float | None,
    *,
    reasoning_tokens: int | None = None,
    role: str | None = None,
    model: str | None = None,
    safe_tool_category: str | None = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_identifier(_SECRET, agent, discriminator, session),
        agent=agent,
        source_session_id=opaque_identifier(_SECRET, "session", session),
        project_key=opaque_identifier(_SECRET, "project", "fixture-project"),
        occurred_at=occurred_at,
        record_kind="usage",
        provenance=f"{agent}.{discriminator}",
        role=role,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
        cache_write=cache_write,
        reasoning_tokens=reasoning_tokens,
        provider_cost_usd=provider_cost_usd,
        safe_tool_category=safe_tool_category,
    )


def test_claude_fixture_maps_usage_shape() -> None:
    record = _fixture("claude-assistant.jsonl")
    message = _object(record["message"])
    usage = _object(message["usage"])
    output_details = _object(usage["output_tokens_details"])

    event = _event(
        "claude-code",
        _string(record["type"]),
        _string(record["sessionId"]),
        _record_timestamp(record),
        _integer(usage["input_tokens"]),
        _integer(usage["output_tokens"]),
        _integer(usage["cache_read_input_tokens"]),
        _integer(usage["cache_creation_input_tokens"]),
        None,
        reasoning_tokens=_integer(output_details["thinking_tokens"]),
        role=_string(message["role"]),
        model=_string(message["model"]),
    )

    assert event.provenance == "claude-code.assistant"
    assert event.occurred_at == datetime(2026, 8, 16, tzinfo=UTC)
    assert event.role == "assistant"
    assert event.model == "fixture-model"
    assert event.tokens_in == 100
    assert event.tokens_out == 20
    assert event.cache_read == 10
    assert event.cache_write == 5
    assert event.reasoning_tokens == 3
    # No reviewed fixture proves a Claude Code provider-cost or tool field.
    assert event.provider_cost_usd is None
    assert event.safe_tool_category is None


def test_codex_fixture_maps_usage_shape() -> None:
    record = _fixture("codex-token-count.jsonl")
    payload = _object(record["payload"])
    info = _object(payload["info"])
    usage = _object(info["total_token_usage"])

    event = _event(
        "codex",
        _string(payload["type"]),
        "fixture-session",
        _record_timestamp(record),
        _integer(usage["input_tokens"]),
        _integer(usage["output_tokens"]),
        _integer(usage["cached_input_tokens"]),
        None,
        None,
        reasoning_tokens=_integer(usage["reasoning_output_tokens"]),
    )

    assert event.provenance == "codex.token_count"
    assert event.occurred_at == datetime(2026, 8, 16, tzinfo=UTC)
    # The reviewed Codex fixture carries no role or model field at all;
    # pinning the absence keeps an unproven capability from creeping in.
    assert event.role is None
    assert event.model is None
    assert event.tokens_in == 100
    assert event.tokens_out == 20
    assert event.cache_read == 10
    assert event.cache_write is None
    assert event.reasoning_tokens == 3
    assert event.provider_cost_usd is None
    assert event.safe_tool_category is None


def test_pi_fixture_maps_usage_shape() -> None:
    record = _fixture("pi-message.jsonl")
    message = _object(record["message"])
    usage = _object(message["usage"])
    cost = _object(usage["cost"])

    event = _event(
        "pi",
        _string(record["type"]),
        "fixture-session",
        _record_timestamp(record),
        _integer(usage["input"]),
        _integer(usage["output"]),
        _integer(usage["cacheRead"]),
        _integer(usage["cacheWrite"]),
        _number(cost["total"]),
        role=_string(message["role"]),
        model=_string(message["model"]),
    )

    assert event.provenance == "pi.message"
    assert event.occurred_at == datetime(2026, 8, 16, tzinfo=UTC)
    assert event.role == "assistant"
    assert event.model == "fixture-model"
    assert event.tokens_in == 100
    assert event.tokens_out == 20
    assert event.cache_read == 10
    assert event.cache_write == 5
    assert event.provider_cost_usd == 0.032
    # No reviewed fixture proves a Pi reasoning-token or tool field.
    assert event.reasoning_tokens is None
    assert event.safe_tool_category is None


def test_omp_fixture_maps_usage_shape() -> None:
    record = _fixture("omp-message.jsonl")
    message = _object(record["message"])
    usage = _object(message["usage"])
    cost = _object(usage["cost"])

    event = _event(
        "omp",
        _string(record["type"]),
        "fixture-session",
        _record_timestamp(record),
        _integer(usage["input"]),
        _integer(usage["output"]),
        _integer(usage["cacheRead"]),
        _integer(usage["cacheWrite"]),
        _number(cost["total"]),
        role=_string(message["role"]),
        model=_string(message["model"]),
        safe_tool_category=_string(message["toolName"]),
    )

    assert event.provenance == "omp.message"
    assert event.occurred_at == datetime(2026, 8, 16, tzinfo=UTC)
    assert event.role == "assistant"
    assert event.model == "fixture-model"
    assert event.tokens_in == 100
    assert event.tokens_out == 20
    assert event.cache_read == 10
    assert event.cache_write == 5
    assert event.provider_cost_usd == 0.032
    # No reviewed fixture proves an OMP reasoning-token field.
    assert event.reasoning_tokens is None
    assert event.safe_tool_category == "read"
