"""Full-stack contracts for the M3 transactional incremental ingestion service."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

import metermaid.ingest as ingest_module
from metermaid.discover import SourceRoot
from metermaid.domain import ParseOutcome
from metermaid.ingest import discover_candidate_files, ingest_once, read_increment
from metermaid.parsers import ClaudeCodeAdapter
from metermaid.state import (
    load_or_create_secret,
    opaque_identifier,
    resolve_state_paths,
)
from metermaid.store import EventStore

_CODEX_RECORD = (
    b'{"type":"event_msg","timestamp":"2026-08-16T00:00:00Z",'
    b'"payload":{"type":"token_count","info":{"total_token_usage":'
    b'{"input_tokens":100,"output_tokens":20,"cached_input_tokens":10,'
    b'"reasoning_output_tokens":3,"total_tokens":133}}}}\n'
)
_CODEX_RECORD_2 = (
    b'{"type":"event_msg","timestamp":"2026-08-16T00:01:00Z",'
    b'"payload":{"type":"token_count","info":{"total_token_usage":'
    b'{"input_tokens":200,"output_tokens":40}}}}\n'
)
_CODEX_RECORD_3 = (
    b'{"type":"event_msg","timestamp":"2026-08-16T00:02:00Z",'
    b'"payload":{"type":"token_count","info":{"total_token_usage":'
    b'{"input_tokens":300,"output_tokens":60}}}}\n'
)
_CODEX_RECORD_4 = (
    b'{"type":"event_msg","timestamp":"2026-08-16T00:03:00Z",'
    b'"payload":{"type":"token_count","info":{"total_token_usage":'
    b'{"input_tokens":400,"output_tokens":80}}}}\n'
)
_OMP_RECORD = (
    b'{"type":"message","timestamp":"2026-08-16T00:00:00Z","message":'
    b'{"role":"assistant","model":"fixture-model","toolName":"read",'
    b'"usage":{"input":100,"output":20,"cacheRead":10,"cacheWrite":5,'
    b'"cost":{"total":0.032}}}}\n'
)

_CLAUDE_NULL_THINKING_RECORD = (
    b'{"type":"assistant","timestamp":"2026-08-16T00:00:00Z","message":'
    b'{"usage":{"input_tokens":100,"output_tokens":20,'
    b'"output_tokens_details":null}}}\n'
)


def _store(tmp_path: Path) -> tuple[EventStore, bytes]:
    paths = resolve_state_paths(tmp_path / "state")
    secret = load_or_create_secret(paths)
    store = EventStore(paths.database)
    store.initialize()
    return store, secret


def _root(agent: str, directory: Path) -> SourceRoot:
    directory.mkdir(parents=True, exist_ok=True)
    return SourceRoot(
        agent=agent, path=directory, glob_pattern="**/*.jsonl", exists=True
    )


def test_discover_candidate_files_only_globs_existing_documented_roots(
    tmp_path: Path,
) -> None:
    present = tmp_path / "present"
    present.mkdir()
    (present / "a.jsonl").write_bytes(_CODEX_RECORD)
    (present / "ignored.txt").write_bytes(b"not jsonl")
    absent = tmp_path / "absent"
    roots = (
        SourceRoot(agent="codex", path=present, glob_pattern="**/*.jsonl", exists=True),
        SourceRoot(agent="omp", path=absent, glob_pattern="**/*.jsonl", exists=False),
    )

    candidates = discover_candidate_files(roots)

    assert [candidate.path.name for candidate in candidates] == ["a.jsonl"]
    assert candidates[0].agent == "codex"


def test_ingest_once_parses_and_persists_one_event_per_source(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    (codex_dir / "rollout-1.jsonl").write_bytes(_CODEX_RECORD)
    roots = (_root("codex", codex_dir),)

    summary = ingest_once(store, secret, roots=roots)

    assert summary.files_read == 1
    assert summary.events_inserted == 1
    events = store.events()
    assert len(events) == 1
    assert events[0].agent == "codex"
    assert events[0].tokens_in == 100
    assert events[0].tokens_out == 20


def test_append_between_ingests_adds_only_the_new_record(tmp_path: Path) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    session = codex_dir / "rollout-1.jsonl"
    session.write_bytes(_CODEX_RECORD)
    roots = (_root("codex", codex_dir),)

    ingest_once(store, secret, roots=roots)
    with session.open("ab") as handle:
        handle.write(_CODEX_RECORD_2)
    ingest_once(store, secret, roots=roots)

    events = store.events()
    assert len(events) == 2
    assert {event.tokens_in for event in events} == {100, 200}


def test_a_repeated_ingest_of_unchanged_data_inserts_no_duplicate(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    (codex_dir / "rollout-1.jsonl").write_bytes(_CODEX_RECORD)
    roots = (_root("codex", codex_dir),)

    first = ingest_once(store, secret, roots=roots)
    second = ingest_once(store, secret, roots=roots)

    assert first.events_inserted == 1
    assert second.events_inserted == 0
    assert len(store.events()) == 1


def test_an_incomplete_final_line_yields_no_event_until_it_is_completed(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    session = codex_dir / "rollout-1.jsonl"
    session.write_bytes(_CODEX_RECORD[:-1])  # drop the trailing newline
    roots = (_root("codex", codex_dir),)

    partial_summary = ingest_once(store, secret, roots=roots)
    assert partial_summary.events_inserted == 0
    assert store.events() == []

    with session.open("ab") as handle:
        handle.write(b"\n")
    completed_summary = ingest_once(store, secret, roots=roots)

    assert completed_summary.events_inserted == 1
    assert len(store.events()) == 1


def test_adapter_revision_recovery_restores_events_without_duplicate_diagnostics(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    session = claude_dir / "session-1.jsonl"
    session.write_bytes(_CLAUDE_NULL_THINKING_RECORD)
    roots = (_root("claude-code", claude_dir),)
    locator = opaque_identifier(secret, "source-locator", str(session))
    prior = read_increment(session, None, locator, secret, adapter_revision=1).watermark
    malformed = ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="malformed"
    )
    store.commit_ingest([], [malformed], prior)
    with session.open("ab") as handle:
        handle.write(b"not-json\n")

    recovered = ingest_once(store, secret, roots=roots)
    repeated = ingest_once(store, secret, roots=roots)

    assert recovered.events_inserted == 1
    assert recovered.diagnostics_recorded == 1
    assert repeated.events_inserted == 0
    assert repeated.diagnostics_recorded == 0
    assert len(store.events()) == 1
    assert store.diagnostics() == [
        malformed,
        ParseOutcome(
            agent="claude-code", discriminator="invalid-json", kind="malformed"
        ),
    ]
    watermark = store.watermark(locator)
    assert watermark is not None
    assert watermark.adapter_revision == 2


def test_bounded_semantic_replay_never_recounts_prior_diagnostics(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    store, secret = _store(tmp_path)
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    session = claude_dir / "session-1.jsonl"
    prefix = b'{"type":"unrecognized","padding":"'
    suffix = b'"}\n'
    unrecognized = (
        prefix
        + b"x" * (len(_CLAUDE_NULL_THINKING_RECORD) - len(prefix) - len(suffix))
        + suffix
    )
    assert len(unrecognized) == len(_CLAUDE_NULL_THINKING_RECORD)
    session.write_bytes(_CLAUDE_NULL_THINKING_RECORD + unrecognized * 4)
    roots = (_root("claude-code", claude_dir),)
    current_adapter = ClaudeCodeAdapter()
    monkeypatch.setitem(
        ingest_module._ADAPTERS,
        "claude-code",
        ClaudeCodeAdapter(adapter_revision=1),
    )
    baseline = ingest_once(store, secret, roots=roots)
    assert baseline.diagnostics_recorded == 4
    monkeypatch.setitem(ingest_module._ADAPTERS, "claude-code", current_adapter)
    monkeypatch.setattr(
        ingest_module, "_MAX_READ_BYTES", len(_CLAUDE_NULL_THINKING_RECORD)
    )

    summaries = [ingest_once(store, secret, roots=roots) for _ in range(5)]

    assert [summary.diagnostics_recorded for summary in summaries] == [0, 0, 0, 0, 0]
    assert sum(summary.events_inserted for summary in summaries) == 0
    assert store.diagnostics() == [
        ParseOutcome(
            agent="claude-code",
            discriminator="unrecognized",
            kind="unsupported",
            count=4,
        )
    ]


def test_bounded_replay_records_rewritten_records_after_completed_prefix(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    store, secret = _store(tmp_path)
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    session = claude_dir / "session-1.jsonl"
    original = b'{"type":"unrecognized","padding":"xxxxxxxx"}\n'
    replacement = b"x" * (len(original) - 1) + b"\n"
    session.write_bytes(original * 5)
    roots = (_root("claude-code", claude_dir),)
    current_adapter = ClaudeCodeAdapter()
    monkeypatch.setitem(
        ingest_module._ADAPTERS,
        "claude-code",
        ClaudeCodeAdapter(adapter_revision=1),
    )
    assert ingest_once(store, secret, roots=roots).diagnostics_recorded == 5
    monkeypatch.setitem(ingest_module._ADAPTERS, "claude-code", current_adapter)
    monkeypatch.setattr(ingest_module, "_MAX_READ_BYTES", len(original))
    assert ingest_once(store, secret, roots=roots).diagnostics_recorded == 0
    session.write_bytes(original + replacement * 4)

    summaries = [ingest_once(store, secret, roots=roots) for _ in range(4)]

    assert [summary.diagnostics_recorded for summary in summaries] == [1, 1, 1, 1]
    assert store.diagnostics() == [
        ParseOutcome(
            agent="claude-code", discriminator="invalid-json", kind="malformed", count=4
        ),
        ParseOutcome(
            agent="claude-code",
            discriminator="unrecognized",
            kind="unsupported",
            count=5,
        ),
    ]


def test_semantic_replay_keeps_diagnostics_for_a_rewritten_generation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    store, secret = _store(tmp_path)
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    session = claude_dir / "session-1.jsonl"
    shared_first_line = b'{"type":"unrecognized"}\n'
    session.write_bytes(shared_first_line * 3)
    roots = (_root("claude-code", claude_dir),)
    current_adapter = ClaudeCodeAdapter()
    monkeypatch.setitem(
        ingest_module._ADAPTERS,
        "claude-code",
        ClaudeCodeAdapter(adapter_revision=1),
    )
    assert ingest_once(store, secret, roots=roots).diagnostics_recorded == 3
    monkeypatch.setitem(ingest_module._ADAPTERS, "claude-code", current_adapter)
    session.write_bytes(shared_first_line + b"not-json\n")

    summary = ingest_once(store, secret, roots=roots)

    assert summary.events_inserted == 0
    assert summary.diagnostics_recorded == 1
    assert store.diagnostics() == [
        ParseOutcome(
            agent="claude-code", discriminator="invalid-json", kind="malformed"
        ),
        ParseOutcome(
            agent="claude-code",
            discriminator="unrecognized",
            kind="unsupported",
            count=3,
        ),
    ]


def test_truncation_then_rotation_both_safely_reread_without_stale_data(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    session = codex_dir / "rollout-1.jsonl"
    session.write_bytes(_CODEX_RECORD + _CODEX_RECORD_2)
    roots = (_root("codex", codex_dir),)
    ingest_once(store, secret, roots=roots)
    assert len(store.events()) == 2

    original_inode = session.stat().st_ino
    session.write_bytes(_CODEX_RECORD_3)
    assert session.stat().st_ino == original_inode
    truncated_summary = ingest_once(store, secret, roots=roots)

    assert truncated_summary.events_inserted == 1
    assert len(store.events()) == 3

    replacement = codex_dir / "rollout-1.jsonl.new"
    replacement.write_bytes(_CODEX_RECORD_4)
    replacement.replace(session)
    assert session.stat().st_ino != original_inode
    rotated_summary = ingest_once(store, secret, roots=roots)

    assert rotated_summary.events_inserted == 1
    assert len(store.events()) == 4


def test_dispatch_routes_each_discovered_file_to_its_own_agents_adapter(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    (codex_dir / "rollout-1.jsonl").write_bytes(_CODEX_RECORD)
    omp_dir = tmp_path / "omp"
    omp_dir.mkdir()
    (omp_dir / "session-1.jsonl").write_bytes(_OMP_RECORD)
    roots = (_root("codex", codex_dir), _root("omp", omp_dir))

    ingest_once(store, secret, roots=roots)

    agents = {event.agent for event in store.events()}
    assert agents == {"codex", "omp"}


def test_invalid_json_line_is_recorded_as_a_diagnostic_not_an_event(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    (codex_dir / "rollout-1.jsonl").write_bytes(b"not-json-at-all\n")
    roots = (_root("codex", codex_dir),)

    summary = ingest_once(store, secret, roots=roots)

    assert summary.events_inserted == 0
    assert summary.diagnostics_recorded == 1
    assert store.events() == []
    diagnostics = store.diagnostics()
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == "malformed"
    assert diagnostics[0].discriminator == "invalid-json"


def test_unsupported_record_shape_is_a_diagnostic_never_a_zero_usage_event(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    (codex_dir / "rollout-1.jsonl").write_bytes(b'{"type":"unrelated"}\n')
    roots = (_root("codex", codex_dir),)

    ingest_once(store, secret, roots=roots)

    assert store.events() == []
    diagnostics = store.diagnostics()
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == "unsupported"


def test_ingest_never_persists_a_value_outside_the_reviewed_adapter_mapping(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    canary = b"PROMPT_CANARY__UNMAPPED_FIELD"
    record = (
        b'{"type":"event_msg","timestamp":"2026-08-16T00:00:00Z","extra":"'
        + canary
        + b'","payload":{"type":"token_count","info":{"total_token_usage":'
        b'{"input_tokens":100,"output_tokens":20}}}}\n'
    )
    (codex_dir / "rollout-1.jsonl").write_bytes(record)
    roots = (_root("codex", codex_dir),)

    ingest_once(store, secret, roots=roots)

    assert len(store.events()) == 1
    assert all(canary.decode() not in str(event) for event in store.events())
    database_bytes = b"".join(
        path.read_bytes()
        for path in (tmp_path / "state").glob("metermaid.sqlite3*")
        if path.is_file()
    )
    assert canary not in database_bytes


def test_watermark_persists_between_ingest_calls(tmp_path: Path) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    session = codex_dir / "rollout-1.jsonl"
    session.write_bytes(_CODEX_RECORD)
    roots = (_root("codex", codex_dir),)

    ingest_once(store, secret, roots=roots)

    locator = opaque_identifier(secret, "source-locator", str(session))
    watermark = store.watermark(locator)
    assert watermark is not None
    assert watermark.complete_offset == len(_CODEX_RECORD)
    assert watermark.observed_size == len(_CODEX_RECORD)


def test_different_generations_at_the_same_offset_never_collide_into_one_event(
    tmp_path: Path,
) -> None:
    """Two generations of the same path can legitimately produce records
    sharing a byte_start (0, after each safe reread) and even the same
    timestamp. Without folding file identity into the source-session id,
    those records would derive the identical event id and the second
    generation's genuinely distinct usage would be silently dropped by
    ``INSERT OR IGNORE`` instead of recorded."""
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    session = codex_dir / "rollout-1.jsonl"
    session.write_bytes(_CODEX_RECORD)
    roots = (_root("codex", codex_dir),)
    ingest_once(store, secret, roots=roots)
    assert len(store.events()) == 1

    colliding_offset_record = (
        b'{"type":"event_msg","timestamp":"2026-08-16T00:00:00Z",'
        b'"payload":{"type":"token_count","info":{"total_token_usage":'
        b'{"input_tokens":999,"output_tokens":999}}}}\n'
    )
    with session.open("wb") as handle:
        handle.write(colliding_offset_record)
    ingest_once(store, secret, roots=roots)

    events = store.events()
    assert len(events) == 2
    assert {event.tokens_in for event in events} == {100, 999}


def test_discovery_skips_a_resolved_match_that_escapes_its_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped_target = outside / "escaped.jsonl"
    escaped_target.write_bytes(_CODEX_RECORD)

    root_dir = tmp_path / "codex"
    root_dir.mkdir()
    (root_dir / "contained.jsonl").write_bytes(_CODEX_RECORD)
    (root_dir / "escape-link.jsonl").symlink_to(escaped_target)
    roots = (_root("codex", root_dir),)

    candidates = discover_candidate_files(roots)

    assert [candidate.path.name for candidate in candidates] == ["contained.jsonl"]


def test_ingest_once_isolates_a_per_file_error_without_losing_other_counts(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    good_before = codex_dir / "a-rollout.jsonl"
    good_before.write_bytes(_CODEX_RECORD)
    bad = codex_dir / "b-rollout.jsonl"
    bad.write_bytes(_CODEX_RECORD_2)
    good_after = codex_dir / "c-rollout.jsonl"
    good_after.write_bytes(_CODEX_RECORD_3)
    roots = (_root("codex", codex_dir),)

    original_ingest_file = ingest_module.ingest_file

    def _flaky(store_arg: EventStore, candidate: object, secret_arg: bytes) -> object:
        if getattr(candidate, "path", None) == bad:
            raise OSError("simulated I/O failure")
        return original_ingest_file(store_arg, candidate, secret_arg)  # type: ignore[arg-type]

    monkeypatch.setattr(ingest_module, "ingest_file", _flaky)

    summary = ingest_module.ingest_once(store, secret, roots=roots)

    assert summary.files_read == 2
    assert summary.events_inserted == 2
    assert len(store.events()) == 2


def test_a_whitespace_only_line_is_skipped_without_a_diagnostic(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    session = codex_dir / "rollout-1.jsonl"
    session.write_bytes(_CODEX_RECORD + b"   \n" + _CODEX_RECORD_2)
    roots = (_root("codex", codex_dir),)

    summary = ingest_once(store, secret, roots=roots)

    assert summary.events_inserted == 2
    assert summary.diagnostics_recorded == 0
    assert len(store.events()) == 2
    assert store.diagnostics() == []
