"""Observable contracts for Metermaid v1 normalized events."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from metermaid.domain import NormalizedEvent, ParseOutcome

_EVENT_ID = "a" * 64
_SESSION_ID = "b" * 64
_PROJECT_KEY = "c" * 64


def test_normalized_event_preserves_missing_numeric_values() -> None:
    event = NormalizedEvent(
        event_id=_EVENT_ID,
        agent="codex",
        source_session_id=_SESSION_ID,
        project_key=_PROJECT_KEY,
        occurred_at=datetime(2026, 8, 16, tzinfo=UTC),
        record_kind="usage",
        provenance="codex.message",
    )

    assert event.tokens_in is None
    assert event.provider_cost_usd is None


def test_normalized_event_has_no_prohibited_persistence_field() -> None:
    field_names = {field.name for field in fields(NormalizedEvent)}

    assert field_names.isdisjoint(
        {"path", "branch", "prompt", "content", "tool_arguments", "tool_result"}
    )


def _event_with_tokens(tokens_in: int) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=_EVENT_ID,
        agent="codex",
        source_session_id=_SESSION_ID,
        project_key=_PROJECT_KEY,
        occurred_at=datetime(2026, 8, 16, tzinfo=UTC),
        record_kind="usage",
        provenance="codex.message",
        tokens_in=tokens_in,
    )


@pytest.mark.parametrize("tokens_in", [-1, 0])
def test_normalized_event_rejects_only_negative_counters(tokens_in: int) -> None:
    if tokens_in < 0:
        with pytest.raises(ValueError, match="tokens_in"):
            _event_with_tokens(tokens_in)
    else:
        assert _event_with_tokens(tokens_in).tokens_in == 0


def test_parse_outcome_rejects_free_text_discriminators() -> None:
    with pytest.raises(ValueError, match="compact structural label"):
        ParseOutcome(
            agent="codex",
            discriminator="PROMPT_CANARY raw text",
            kind="unsupported",
        )


def test_normalized_event_rejects_raw_identity_values() -> None:
    with pytest.raises(ValueError, match="project_key"):
        NormalizedEvent(
            event_id=_EVENT_ID,
            agent="codex",
            source_session_id=_SESSION_ID,
            project_key="/private/PROMPT_CANARY",
            occurred_at=datetime(2026, 8, 16, tzinfo=UTC),
            record_kind="usage",
            provenance="codex.message",
        )
