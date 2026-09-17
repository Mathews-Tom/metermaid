"""Aggregate discovery and parse-outcome contracts for `metermaid doctor`.

Every assertion here checks a count or a compact structural label, never
a path or a source record value — the same safety boundary `doctor`'s
own output must hold.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from metermaid.cli import main
from metermaid.discover import PILOT_AGENTS, SourceRoot
from metermaid.doctor import (
    AgentDiscovery,
    DiscriminatorCount,
    DoctorReport,
    build_doctor_report,
)
from metermaid.evidence import diagnostic_state_fingerprint
from metermaid.ingest import ingest_once
from metermaid.state import load_or_create_secret, resolve_state_paths
from metermaid.store import EventStore

_CODEX_PARSED_RECORD = (
    b'{"type":"event_msg","timestamp":"2026-08-16T00:00:00Z",'
    b'"payload":{"type":"token_count","info":{"total_token_usage":'
    b'{"input_tokens":100,"output_tokens":20}}}}\n'
)
_CODEX_MALFORMED_LINE = b"{not-valid-json\n"
_CODEX_UNSUPPORTED_RECORD = (
    b'{"type":"other-shape","timestamp":"2026-08-16T00:04:00Z"}\n'
)
_OMP_PARSED_RECORD = (
    b'{"type":"message","timestamp":"2026-08-16T00:00:00Z","message":'
    b'{"role":"assistant","model":"fixture-model","toolName":"read",'
    b'"usage":{"input":100,"output":20,"cacheRead":10,"cacheWrite":5,'
    b'"cost":{"total":0.032}}}}\n'
)


def _store(tmp_path: Path) -> tuple[EventStore, bytes]:
    paths = resolve_state_paths(tmp_path / "state")
    secret = load_or_create_secret(paths)
    store = EventStore(paths.database)
    store.initialize()
    return store, secret


def _root(
    agent: str, directory: Path, *, exists: bool = True, glob: str = "**/*.jsonl"
) -> SourceRoot:
    if exists:
        directory.mkdir(parents=True, exist_ok=True)
    return SourceRoot(agent=agent, path=directory, glob_pattern=glob, exists=exists)


def test_report_covers_every_pilot_agent_even_with_no_discovered_source(
    tmp_path: Path,
) -> None:
    store, _secret = _store(tmp_path)

    report = build_doctor_report(store, roots=())

    assert {agent.agent for agent in report.discovery} == set(PILOT_AGENTS)
    assert all(agent.enabled for agent in report.discovery)
    assert all(agent.roots_documented == 0 for agent in report.discovery)
    assert all(agent.roots_present == 0 for agent in report.discovery)
    assert all(agent.candidate_files == 0 for agent in report.discovery)
    assert report.counts == ()


def test_a_root_existing_on_disk_is_discovery_not_enabled_capability(
    tmp_path: Path,
) -> None:
    """A present root is a raw filesystem fact; `enabled` reflects adapter
    registration and must stay true regardless of what discovery observes."""
    store, _secret = _store(tmp_path)
    roots = (
        _root("codex", tmp_path / "present"),
        _root("pi", tmp_path / "absent", exists=False),
    )

    report = build_doctor_report(store, roots=roots)

    by_agent = {agent.agent: agent for agent in report.discovery}
    assert by_agent["codex"].roots_present == 1
    assert by_agent["codex"].roots_documented == 1
    assert by_agent["codex"].candidate_files == 0
    assert by_agent["codex"].enabled is True
    assert by_agent["pi"].roots_present == 0
    assert by_agent["pi"].roots_documented == 1
    assert by_agent["pi"].enabled is True


def test_candidate_file_counts_are_aggregate_never_raw_paths(tmp_path: Path) -> None:
    store, _secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    (codex_dir / "rollout-1.jsonl").write_bytes(_CODEX_PARSED_RECORD)
    (codex_dir / "rollout-2.jsonl").write_bytes(_CODEX_PARSED_RECORD)
    roots = (_root("codex", codex_dir),)

    report = build_doctor_report(store, roots=roots)

    by_agent = {agent.agent: agent for agent in report.discovery}
    assert by_agent["codex"].candidate_files == 2
    assert {field.name for field in fields(AgentDiscovery)} == {
        "agent",
        "enabled",
        "roots_documented",
        "roots_present",
        "candidate_files",
    }


def test_parsed_malformed_and_unsupported_counts_group_by_agent_and_discriminator(
    tmp_path: Path,
) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    (codex_dir / "rollout-1.jsonl").write_bytes(
        _CODEX_PARSED_RECORD + _CODEX_MALFORMED_LINE + _CODEX_UNSUPPORTED_RECORD
    )
    omp_dir = tmp_path / "omp"
    omp_dir.mkdir()
    (omp_dir / "session.jsonl").write_bytes(_OMP_PARSED_RECORD)
    roots = (_root("codex", codex_dir), _root("omp", omp_dir))

    ingest_once(store, secret, roots=roots)
    report = build_doctor_report(store, roots=roots)

    by_key = {
        (row.agent, row.discriminator, row.kind): row.count for row in report.counts
    }
    assert by_key[("codex", "codex.token_count", "parsed")] == 1
    assert by_key[("codex", "invalid-json", "malformed")] == 1
    assert by_key[("codex", "other-shape", "unsupported")] == 1
    assert by_key[("omp", "omp.message", "parsed")] == 1


def test_a_repeated_report_after_reingest_never_double_counts(tmp_path: Path) -> None:
    store, secret = _store(tmp_path)
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    (codex_dir / "rollout-1.jsonl").write_bytes(_CODEX_PARSED_RECORD)
    roots = (_root("codex", codex_dir),)

    ingest_once(store, secret, roots=roots)
    ingest_once(store, secret, roots=roots)
    report = build_doctor_report(store, roots=roots)

    parsed_counts = [row.count for row in report.counts if row.kind == "parsed"]
    assert parsed_counts == [1]


def test_doctor_dataclasses_carry_no_raw_path_or_free_text_field() -> None:
    for cls in (AgentDiscovery, DiscriminatorCount, DoctorReport):
        for field in fields(cls):
            assert field.type is not Path
    assert {field.name for field in fields(DiscriminatorCount)} == {
        "agent",
        "discriminator",
        "kind",
        "count",
    }
    assert {field.name for field in fields(DoctorReport)} == {"discovery", "counts"}


def _run_doctor(
    monkeypatch: MonkeyPatch, tmp_path: Path, *acknowledge: str
) -> tuple[EventStore, str]:
    home = tmp_path / "home"
    sessions = home / ".codex" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "rollout-1.jsonl").write_bytes(_CODEX_PARSED_RECORD)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    data_dir = tmp_path / "state"
    monkeypatch.setattr(
        "sys.argv",
        ["metermaid", "doctor", "--data-dir", str(data_dir), *acknowledge],
    )

    main()

    store = EventStore(resolve_state_paths(data_dir).database)
    store.initialize()
    return store, ""


def test_doctor_records_no_review_without_the_acknowledge_flag(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, _ = _run_doctor(monkeypatch, tmp_path)
    out = capsys.readouterr().out

    assert store.diagnostic_reviews() == []
    assert "Parse outcomes" in out
    assert "Acknowledged" not in out


def test_doctor_acknowledgement_binds_to_the_displayed_state(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, _ = _run_doctor(monkeypatch, tmp_path, "--acknowledge")
    out = capsys.readouterr().out
    secret = load_or_create_secret(resolve_state_paths(tmp_path / "state"))

    reviews = store.diagnostic_reviews()
    assert len(reviews) == 1
    assert "Parse outcomes" in out
    assert "Acknowledged" in out

    displayed = build_doctor_report(store).counts
    assert reviews[0].state_fingerprint == diagnostic_state_fingerprint(
        secret, displayed
    )
    assert reviews[0].diagnostic_count == sum(row.count for row in displayed)


def test_acknowledgement_does_not_carry_over_to_a_changed_state(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, _ = _run_doctor(monkeypatch, tmp_path, "--acknowledge")
    capsys.readouterr()
    secret = load_or_create_secret(resolve_state_paths(tmp_path / "state"))
    acknowledged = store.diagnostic_reviews()[0].state_fingerprint

    widened = build_doctor_report(store).counts + (
        DiscriminatorCount(
            agent="omp", discriminator="model_usage", kind="unsupported", count=5
        ),
    )

    assert diagnostic_state_fingerprint(secret, widened) != acknowledged
