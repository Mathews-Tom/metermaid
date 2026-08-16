"""Documented per-agent source-discovery roots for Metermaid v1.

Discovery reports every documented candidate transcript root for each
of the four pilot agents (Claude Code, Codex, Pi, and OMP), mirroring
the layouts ``src/metermaid/platform.py`` and ``README.md`` already
document for the legacy watcher: the current and legacy Claude Code
project directories, an explicit ``$CODEX_HOME`` override plus the
default Codex sessions directory, and every WSL/Windows-side home
alongside the native one. Home-directory expansion reuses
``platform.home_roots()`` rather than re-deriving it.

A root existing on disk is a raw filesystem fact only; it never
classifies a source as an enabled capability. Enabling a source
additionally requires a reviewed redacted fixture and a passing
adapter contract test (see ``tests/fixtures/m3`` and
``tests/test_source_schema_evidence.py``). Discovery does not walk a
root's contents, open a file, or hold incremental read state — source
traversal and watermark management belong to the ingestion service.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .platform import home_roots as _platform_home_roots

PILOT_AGENTS: tuple[str, ...] = ("claude-code", "codex", "pi", "omp")
"""The four agents in scope for the M3 pilot, in the approved design's order."""

_JSONL_GLOB = "**/*.jsonl"
_CODEX_ROLLOUT_GLOB = "**/rollout-*.jsonl"


@dataclass(frozen=True, slots=True)
class SourceRoot:
    """One documented candidate transcript root for one agent.

    An agent may have more than one documented root: Claude Code has a
    current and a legacy project-directory layout, Codex may add an
    explicit ``$CODEX_HOME`` override, and every agent gets one root per
    scanned home directory (native plus any WSL/Windows-side home).
    ``exists`` reports only whether that specific root directory is
    present on disk; it is not, and must never be treated as, an
    enabled-capability flag — that determination requires a reviewed
    fixture and a passing adapter contract test.
    """

    agent: str
    path: Path
    glob_pattern: str
    exists: bool


def _root(agent: str, path: Path, glob_pattern: str) -> SourceRoot:
    return SourceRoot(
        agent=agent, path=path, glob_pattern=glob_pattern, exists=path.is_dir()
    )


def documented_source_roots(
    home_roots: Sequence[Path] | None = None,
) -> tuple[SourceRoot, ...]:
    """Return every documented candidate root for each of the four pilot agents.

    ``home_roots`` overrides the scanned home directories for tests; the
    default reuses ``platform.home_roots()`` (native home plus any
    WSL/Windows-side home). This performs at most one directory-presence
    check per candidate root and never lists, opens, or reads a source
    file.
    """
    homes = (
        tuple(home_roots) if home_roots is not None else tuple(_platform_home_roots())
    )
    roots: list[SourceRoot] = []

    for home in homes:
        roots.append(
            _root("claude-code", home / ".config" / "claude" / "projects", _JSONL_GLOB)
        )
        roots.append(_root("claude-code", home / ".claude" / "projects", _JSONL_GLOB))

    codex_home = os.environ.get("CODEX_HOME", "")
    if codex_home:
        roots.append(_root("codex", Path(codex_home) / "sessions", _CODEX_ROLLOUT_GLOB))
    for home in homes:
        roots.append(_root("codex", home / ".codex" / "sessions", _CODEX_ROLLOUT_GLOB))

    for home in homes:
        roots.append(_root("pi", home / ".pi" / "agent" / "sessions", _JSONL_GLOB))

    for home in homes:
        roots.append(_root("omp", home / ".omp" / "agent" / "sessions", _JSONL_GLOB))

    return tuple({(root.agent, root.path): root for root in roots}.values())
