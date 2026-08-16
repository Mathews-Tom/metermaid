"""Tests for Claude Code JSONL transcript parsing."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from metermaid.parsers.claude import parse_claude_transcript


def _write_jsonl(path: Path, entries: Iterable[object]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def test_cost_usd_summing(tmp_path: Path) -> None:
    entries = [
        {
            "timestamp": "2025-01-01T00:00:00Z",
            "costUSD": 0.05,
            "message": {
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "model": "claude-sonnet-4-6",
            },
        },
        {
            "timestamp": "2025-01-01T00:01:00Z",
            "costUSD": 0.10,
            "message": {
                "usage": {"input_tokens": 200, "output_tokens": 80},
                "model": "claude-sonnet-4-6",
            },
        },
    ]
    f = _write_jsonl(tmp_path / "session-abc123.jsonl", entries)
    snap = parse_claude_transcript(f)
    assert snap is not None
    assert snap.cost_usd == round(0.05 + 0.10, 6)
    assert snap.tokens_in == 300
    assert snap.tokens_out == 130
    assert snap.model == "claude-sonnet-4-6"
    assert snap.provider == "claude"
    assert snap.session_id == "session-abc1"


def test_sidechain_tracked_separately(tmp_path: Path) -> None:
    """Sidechain entries are tracked in sc_* fields, not mixed into main chain."""
    entries = [
        {
            "timestamp": "2025-01-01T00:00:00Z",
            "costUSD": 0.05,
            "message": {
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "model": "claude-opus-4-6",
            },
        },
        {
            "timestamp": "2025-01-01T00:01:00Z",
            "isSidechain": True,
            "costUSD": 0.02,
            "message": {
                "usage": {"input_tokens": 500, "output_tokens": 200},
                "model": "claude-haiku-4-5-20251001",
            },
        },
        {
            "timestamp": "2025-01-01T00:02:00Z",
            "isApiErrorMessage": True,
            "costUSD": 888.0,
            "message": {
                "usage": {"input_tokens": 8888, "output_tokens": 8888},
                "model": "claude-sonnet-4-6",
            },
        },
    ]
    f = _write_jsonl(tmp_path / "session-abc123.jsonl", entries)
    snap = parse_claude_transcript(f)
    assert snap is not None
    # Main chain: only first entry
    assert snap.cost_usd == 0.05
    assert snap.tokens_in == 100
    assert snap.tokens_out == 50
    assert snap.model == "claude-opus-4-6"
    # Sidechain: second entry (error entry is dropped entirely)
    assert snap.sc_tokens_in == 500
    assert snap.sc_tokens_out == 200
    assert snap.sc_cost_usd == 0.02
    assert "claude-haiku-4-5-20251001" in snap.sc_models


def test_synthetic_filtered(tmp_path: Path) -> None:
    """<synthetic> model entries are filtered out completely."""
    entries = [
        {
            "timestamp": "2025-01-01T00:00:00Z",
            "message": {
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "model": "claude-opus-4-6",
            },
        },
        {
            "timestamp": "2025-01-01T00:01:00Z",
            "message": {
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "model": "<synthetic>",
            },
        },
    ]
    f = _write_jsonl(tmp_path / "session-synth.jsonl", entries)
    snap = parse_claude_transcript(f)
    assert snap is not None
    assert snap.model == "claude-opus-4-6"
    assert snap.tokens_in == 100


def test_empty_input(tmp_path: Path) -> None:
    f = tmp_path / "empty.jsonl"
    f.write_text("")
    assert parse_claude_transcript(f) is None


def test_malformed_lines(tmp_path: Path) -> None:
    content = "not json at all\n{bad json\n"
    content += json.dumps(
        {
            "timestamp": "2025-01-01T00:00:00Z",
            "message": {
                "usage": {"input_tokens": 50, "output_tokens": 25},
                "model": "claude-opus-4-6",
            },
        }
    )
    f = tmp_path / "malformed.jsonl"
    f.write_text(content)
    snap = parse_claude_transcript(f)
    assert snap is not None
    assert snap.tokens_in == 50
    assert snap.tokens_out == 25


def test_no_usage_entries(tmp_path: Path) -> None:
    entries = [
        {"timestamp": "2025-01-01T00:00:00Z", "type": "human"},
        {"timestamp": "2025-01-01T00:01:00Z", "message": {"content": "hello"}},
    ]
    f = _write_jsonl(tmp_path / "nousage.jsonl", entries)
    assert parse_claude_transcript(f) is None


def test_cache_tokens(tmp_path: Path) -> None:
    entries = [
        {
            "timestamp": "2025-01-01T00:00:00Z",
            "message": {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 5000,
                    "cache_creation_input_tokens": 3000,
                },
                "model": "claude-sonnet-4-6",
            },
        },
    ]
    f = _write_jsonl(tmp_path / "cached.jsonl", entries)
    snap = parse_claude_transcript(f)
    assert snap is not None
    assert snap.cache_read == 5000
    assert snap.cache_write == 3000
    assert snap.ctx_tokens == 1000 + 5000 + 3000
    # tokens_in includes cache: input_tokens + cache_read + cache_creation
    assert snap.tokens_in == 1000 + 5000 + 3000


def test_tokens_in_includes_cache_across_turns(tmp_path: Path) -> None:
    """tokens_in accumulates input + cache_read + cache_creation across all turns."""
    entries = [
        {
            "timestamp": "2025-01-01T00:00:00Z",
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 100,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 20000,
                },
                "model": "claude-opus-4-6",
            },
        },
        {
            "timestamp": "2025-01-01T00:01:00Z",
            "message": {
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 18000,
                    "cache_creation_input_tokens": 2000,
                },
                "model": "claude-opus-4-6",
            },
        },
    ]
    f = _write_jsonl(tmp_path / "multi-turn.jsonl", entries)
    snap = parse_claude_transcript(f)
    assert snap is not None
    # Turn 1: 10 + 0 + 20000 = 20010
    # Turn 2: 5 + 18000 + 2000 = 20005
    assert snap.tokens_in == 20010 + 20005
    assert snap.tokens_out == 300
    assert snap.cache_read == 18000  # cumulative
    assert snap.cache_write == 22000  # cumulative (20000 + 2000)
