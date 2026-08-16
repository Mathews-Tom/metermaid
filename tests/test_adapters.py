"""Structural contracts for the M3 pure source-adapter protocol."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from pytest import MonkeyPatch

from metermaid.adapters import (
    AdapterOutcome,
    CompleteRecord,
    RecordContext,
    SourceAdapter,
)
from metermaid.discover import PILOT_AGENTS
from metermaid.domain import NormalizedEvent, ParseOutcome
from metermaid.state import event_identifier, opaque_identifier

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "m3"
_SECRET = b"fixture-secret-for-adapter-protocol"

_FIXTURE_EVIDENCE: dict[str, tuple[str, str]] = {
    "claude-code": (
        "claude-assistant.jsonl",
        "test_claude_fixture_maps_usage_shape",
    ),
    "codex": ("codex-token-count.jsonl", "test_codex_fixture_maps_usage_shape"),
    "pi": ("pi-message.jsonl", "test_pi_fixture_maps_usage_shape"),
    "omp": ("omp-message.jsonl", "test_omp_fixture_maps_usage_shape"),
}


@dataclass(frozen=True, slots=True)
class _ProtocolProbe:
    """A minimal concrete implementation used only to prove the protocol's
    shape is satisfiable by pure code. It does not represent, and must
    never be mistaken for, a real per-agent adapter: PR2 supplies those."""

    agent: str

    def parse(
        self, record: CompleteRecord, *, context: RecordContext, secret: bytes
    ) -> AdapterOutcome:
        del record, secret
        return ParseOutcome(
            agent=self.agent,
            discriminator="probe",
            kind="unsupported",
        )


class _MissingParseMethod:
    """An object that declares ``agent`` but not ``parse``; must fail the check."""

    agent = "codex"


def test_pilot_agents_are_all_accepted_by_the_domain_agent_contract() -> None:
    for agent in PILOT_AGENTS:
        NormalizedEvent(
            event_id=event_identifier(_SECRET, agent, "probe"),
            agent=agent,
            source_session_id=opaque_identifier(_SECRET, "session", "probe"),
            project_key=opaque_identifier(_SECRET, "project", "probe"),
            occurred_at=datetime(2026, 8, 16, tzinfo=UTC),
            record_kind="usage",
            provenance=f"{agent}.probe",
        )


def test_adapter_outcome_covers_exactly_normalized_event_and_parse_outcome() -> None:
    assert set(get_args(AdapterOutcome)) == {NormalizedEvent, ParseOutcome}


def test_conforming_probe_satisfies_the_protocol_structurally() -> None:
    probe = _ProtocolProbe(agent="codex")

    assert isinstance(probe, SourceAdapter)


def test_object_without_parse_method_fails_the_protocol_check() -> None:
    assert not isinstance(_MissingParseMethod(), SourceAdapter)


def test_a_protocol_conforming_probe_can_satisfy_purity_without_forbidden_calls(
    monkeypatch: MonkeyPatch,
) -> None:
    """Proves the protocol *can* be implemented purely, using a synthetic
    probe. It does not, and cannot, prove any real PR2 adapter is pure —
    that requires the same guard exercised against a real implementation."""

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("adapter.parse must not perform this operation")

    monkeypatch.setattr(Path, "open", _forbidden)
    monkeypatch.setattr("builtins.open", _forbidden)
    monkeypatch.setattr(sqlite3, "connect", _forbidden)
    monkeypatch.setattr(time, "time", _forbidden)
    monkeypatch.setattr(time, "monotonic", _forbidden)
    monkeypatch.setattr(time, "time_ns", _forbidden)
    # datetime.datetime.now cannot be monkeypatched (immutable C type); the
    # probe below never imports datetime, so the remaining guards suffice.
    monkeypatch.setattr(os, "stat", _forbidden)

    probe = _ProtocolProbe(agent="pi")
    context = RecordContext(source_session_id="s" * 64, project_key="p" * 64)
    outcome = probe.parse({"type": "message"}, context=context, secret=_SECRET)

    assert isinstance(outcome, ParseOutcome)


def test_every_pilot_agent_has_a_named_reviewed_fixture_contract_test() -> None:
    """Checks that a named, callable contract test exists per agent — not
    that it currently passes; running it is the pytest collection step's
    job, not this cross-check's."""
    import tests.test_source_schema_evidence as evidence

    assert set(PILOT_AGENTS) == set(_FIXTURE_EVIDENCE)
    for agent, (fixture_name, test_name) in _FIXTURE_EVIDENCE.items():
        assert (FIXTURE_ROOT / fixture_name).is_file(), f"missing fixture for {agent}"
        contract_test = getattr(evidence, test_name, None)
        assert callable(contract_test), f"missing contract test for {agent}"
