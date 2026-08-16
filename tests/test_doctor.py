"""Aggregate discovery and parse-outcome contracts for `metermaid doctor`.

Every assertion here checks a count or a compact structural label, never
a path or a source record value — the same safety boundary `doctor`'s
own output must hold.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from metermaid.discover import PILOT_AGENTS, SourceRoot
from metermaid.doctor import (
    AgentDiscovery,
    DiscriminatorCount,
    DoctorReport,
    build_doctor_report,
)
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
