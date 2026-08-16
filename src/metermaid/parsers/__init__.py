"""Transcript parsers — the only modules that touch JSONL structure."""

from __future__ import annotations

from .claude import parse_claude_transcript
from .codex import parse_codex_session
from .pilot_adapters import ClaudeCodeAdapter, CodexAdapter, OmpAdapter, PiAdapter

__all__ = [
    "parse_claude_transcript",
    "parse_codex_session",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "OmpAdapter",
    "PiAdapter",
]
