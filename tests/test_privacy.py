"""Privacy canaries for Metermaid v1 persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from metermaid.domain import FileWatermark, NormalizedEvent, ParseOutcome
from metermaid.state import event_identifier, opaque_identifier
from metermaid.store import EventStore


def test_store_never_persists_seeded_prohibited_values(tmp_path: Path) -> None:
    forbidden = "PROMPT_CANARY__RAW_PATH_CANARY__TOOL_ARGUMENT_CANARY"
    database = tmp_path / "metermaid.sqlite3"
    store = EventStore(database)
    store.initialize()
    secret = b"x" * 32
    source_session_id = opaque_identifier(secret, "session", "session-1")
    source_locator = opaque_identifier(secret, "source", "/private/transcript.jsonl")
    file_identity = opaque_identifier(secret, "identity", "device:inode")
    store.commit_ingest(
        [
            NormalizedEvent(
                event_id=event_identifier(secret, "codex", "record-1"),
                agent="codex",
                source_session_id=source_session_id,
                project_key=opaque_identifier(secret, "project", forbidden),
                occurred_at=datetime(2026, 8, 16, tzinfo=UTC),
                record_kind="usage",
                provenance="codex.response",
            )
        ],
        [ParseOutcome(agent="codex", discriminator="response", kind="parsed")],
        FileWatermark(
            source_locator=source_locator,
            file_identity=file_identity,
            observed_size=100,
            modified_ns=50,
            complete_offset=80,
        ),
    )

    persisted_bytes = b"".join(
        path.read_bytes()
        for path in tmp_path.glob("metermaid.sqlite3*")
        if path.is_file()
    )
    assert forbidden.encode() not in persisted_bytes
    assert all(forbidden not in str(event) for event in store.events())


def test_opaque_identifiers_are_stable_and_namespaced() -> None:
    secret = b"x" * 32

    assert opaque_identifier(
        secret, "project", "/private/workspace"
    ) == opaque_identifier(secret, "project", "/private/workspace")
    assert opaque_identifier(
        secret, "project", "/private/workspace"
    ) != opaque_identifier(secret, "source", "/private/workspace")


def test_public_write_contract_rejects_seeded_source_values() -> None:
    forbidden = "PROMPT_CANARY raw source value"
    secret = b"x" * 32

    with pytest.raises(ValueError):
        NormalizedEvent(
            event_id=event_identifier(secret, "event"),
            agent="codex",
            source_session_id=forbidden,
            project_key=forbidden,
            occurred_at=datetime(2026, 8, 16, tzinfo=UTC),
            record_kind="usage",
            provenance="codex.response",
        )
    with pytest.raises(ValueError):
        ParseOutcome(agent="codex", discriminator=forbidden, kind="unsupported")
    with pytest.raises(ValueError):
        FileWatermark(
            source_locator=forbidden,
            file_identity=forbidden,
            observed_size=100,
            modified_ns=50,
            complete_offset=80,
        )
