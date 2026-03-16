"""Tests for Codex CLI session JSONL parsing."""

from __future__ import annotations

import json
from pathlib import Path

from codetrack.parsers.codex import parse_codex_session


def _write_jsonl(path: Path, entries: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def test_token_count_deltas(tmp_path: Path) -> None:
    entries = [
        {"timestamp": "2025-06-01T10:00:00Z",
         "turn_context": {"model": "gpt-4.1"},
         "payload": {"type": "token_count", "input_tokens": 100, "output_tokens": 50, "cached_input_tokens": 20}},
        {"timestamp": "2025-06-01T10:01:00Z",
         "payload": {"type": "token_count", "input_tokens": 250, "output_tokens": 120, "cached_input_tokens": 40}},
    ]
    f = _write_jsonl(tmp_path / "codex-session1.jsonl", entries)
    snap = parse_codex_session(f)
    assert snap is not None
    assert snap.tokens_in == 250  # 100 + (250-100)
    assert snap.tokens_out == 120  # 50 + (120-50)
    assert snap.cache_read == 40  # 20 + (40-20)
    assert snap.model == "gpt-4.1"
    assert snap.provider == "codex"


def test_legacy_message_usage_fallback(tmp_path: Path) -> None:
    entries = [
        {"timestamp": "2025-06-01T10:00:00Z",
         "message": {
             "model": "gpt-4.1-mini",
             "usage": {"prompt_tokens": 500, "completion_tokens": 200,
                       "prompt_tokens_details": {"cached_tokens": 100}},
         }},
        {"timestamp": "2025-06-01T10:01:00Z",
         "message": {
             "model": "gpt-4.1-mini",
             "usage": {"prompt_tokens": 300, "completion_tokens": 150,
                       "prompt_tokens_details": {"cached_tokens": 50}},
         }},
    ]
    f = _write_jsonl(tmp_path / "codex-legacy.jsonl", entries)
    snap = parse_codex_session(f)
    assert snap is not None
    assert snap.tokens_in == 800  # 500 + 300
    assert snap.tokens_out == 350  # 200 + 150
    assert snap.cache_read == 150  # 100 + 50
    assert snap.model == "gpt-4.1-mini"


def test_missing_model(tmp_path: Path) -> None:
    entries = [
        {"timestamp": "2025-06-01T10:00:00Z",
         "payload": {"type": "token_count", "input_tokens": 100, "output_tokens": 50}},
    ]
    f = _write_jsonl(tmp_path / "nomodel.jsonl", entries)
    snap = parse_codex_session(f)
    assert snap is not None
    assert snap.model == "unknown"


def test_empty_session(tmp_path: Path) -> None:
    f = tmp_path / "empty.jsonl"
    f.write_text("")
    assert parse_codex_session(f) is None


def test_no_token_data(tmp_path: Path) -> None:
    entries = [
        {"timestamp": "2025-06-01T10:00:00Z", "type": "codex.conversation_starts", "model": "gpt-4.1"},
    ]
    f = _write_jsonl(tmp_path / "notokens.jsonl", entries)
    assert parse_codex_session(f) is None


def test_token_count_preferred_over_legacy(tmp_path: Path) -> None:
    """When both token_count events and message.usage exist, prefer token_count."""
    entries = [
        {"timestamp": "2025-06-01T10:00:00Z",
         "turn_context": {"model": "gpt-4.1"},
         "payload": {"type": "token_count", "input_tokens": 100, "output_tokens": 50}},
        {"timestamp": "2025-06-01T10:01:00Z",
         "message": {"model": "gpt-4.1-mini", "usage": {"prompt_tokens": 9999, "completion_tokens": 9999}}},
    ]
    f = _write_jsonl(tmp_path / "both.jsonl", entries)
    snap = parse_codex_session(f)
    assert snap is not None
    assert snap.tokens_in == 100  # from token_count, not legacy
    assert snap.model == "gpt-4.1"  # from turn_context
