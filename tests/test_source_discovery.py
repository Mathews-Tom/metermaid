"""Contracts for the M3 documented per-agent source-discovery roots."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from pytest import MonkeyPatch

from metermaid.discover import PILOT_AGENTS, SourceRoot, documented_source_roots


def test_documented_source_roots_covers_every_pilot_agent(tmp_path: Path) -> None:
    roots = documented_source_roots(home_roots=[tmp_path])

    assert {root.agent for root in roots} == set(PILOT_AGENTS)


def test_claude_code_includes_both_current_and_legacy_project_roots(
    tmp_path: Path,
) -> None:
    claude_roots = [
        root
        for root in documented_source_roots(home_roots=[tmp_path])
        if root.agent == "claude-code"
    ]
    paths = {root.path for root in claude_roots}

    assert tmp_path / ".config" / "claude" / "projects" in paths
    assert tmp_path / ".claude" / "projects" in paths
    assert all(root.glob_pattern == "**/*.jsonl" for root in claude_roots)


def test_codex_root_falls_back_to_the_default_directory_without_codex_home(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)

    codex_roots = [
        root
        for root in documented_source_roots(home_roots=[tmp_path])
        if root.agent == "codex"
    ]

    assert {root.path for root in codex_roots} == {tmp_path / ".codex" / "sessions"}
    assert all(root.glob_pattern == "**/rollout-*.jsonl" for root in codex_roots)


def test_codex_root_includes_an_explicit_codex_home_override(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    codex_home = tmp_path / "custom-codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    codex_roots = [
        root
        for root in documented_source_roots(home_roots=[tmp_path])
        if root.agent == "codex"
    ]
    paths = {root.path for root in codex_roots}

    assert codex_home / "sessions" in paths
    assert tmp_path / ".codex" / "sessions" in paths


def test_codex_roots_deduplicate_an_explicit_default_override(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))

    codex_paths = [
        root.path
        for root in documented_source_roots(home_roots=[tmp_path])
        if root.agent == "codex"
    ]

    assert codex_paths == [tmp_path / ".codex" / "sessions"]


def test_pi_and_omp_each_use_one_root_per_scanned_home(tmp_path: Path) -> None:
    roots = {
        root.agent: root
        for root in documented_source_roots(home_roots=[tmp_path])
        if root.agent in {"pi", "omp"}
    }

    assert roots["pi"].path == tmp_path / ".pi" / "agent" / "sessions"
    assert roots["pi"].glob_pattern == "**/*.jsonl"
    assert roots["omp"].path == tmp_path / ".omp" / "agent" / "sessions"
    assert roots["omp"].glob_pattern == "**/*.jsonl"


def test_multiple_home_roots_multiply_every_per_home_agent_root(
    tmp_path: Path,
) -> None:
    native_home = tmp_path / "native-home"
    wsl_home = tmp_path / "wsl-home"
    native_home.mkdir()
    wsl_home.mkdir()

    roots = documented_source_roots(home_roots=[native_home, wsl_home])
    pi_paths = {root.path for root in roots if root.agent == "pi"}

    assert pi_paths == {
        native_home / ".pi" / "agent" / "sessions",
        wsl_home / ".pi" / "agent" / "sessions",
    }


def test_default_discovery_call_uses_platform_home_roots(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("metermaid.discover._platform_home_roots", lambda: (tmp_path,))
    monkeypatch.delenv("CODEX_HOME", raising=False)

    roots = documented_source_roots()

    assert all(root.path.is_relative_to(tmp_path) for root in roots)


def test_source_root_existence_is_a_raw_filesystem_fact(tmp_path: Path) -> None:
    (tmp_path / ".pi" / "agent" / "sessions").mkdir(parents=True)

    roots = {
        (root.agent, root.path): root.exists
        for root in documented_source_roots(home_roots=[tmp_path])
    }

    assert roots[("pi", tmp_path / ".pi" / "agent" / "sessions")] is True
    assert roots[("omp", tmp_path / ".omp" / "agent" / "sessions")] is False
    assert roots[("codex", tmp_path / ".codex" / "sessions")] is False
    assert roots[("claude-code", tmp_path / ".claude" / "projects")] is False


def test_source_root_has_no_enabled_or_capability_field() -> None:
    field_names = {field.name for field in fields(SourceRoot)}

    assert field_names == {"agent", "path", "glob_pattern", "exists"}
    assert field_names.isdisjoint({"enabled", "capability", "supported"})


def test_documented_source_roots_never_lists_directory_contents(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".claude" / "projects").mkdir(parents=True)

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("discovery must not enumerate directory contents")

    monkeypatch.setattr(Path, "iterdir", _forbidden)
    monkeypatch.setattr(Path, "glob", _forbidden)
    monkeypatch.setattr(Path, "rglob", _forbidden)
    monkeypatch.setattr(Path, "open", _forbidden)

    roots = documented_source_roots(home_roots=[tmp_path])

    assert any(root.exists for root in roots)
