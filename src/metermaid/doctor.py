"""Aggregate, text-free discovery and parse-outcome reporting for ``doctor``.

This module distinguishes two facts that ``metermaid doctor`` must never
conflate: a documented source root existing on disk is a raw filesystem
observation, while an agent being *enabled* requires a registered,
fixture-backed adapter (see :func:`metermaid.ingest.enabled_agents`).
Every value produced here is a count or a compact structural label —
never a path, a source record, a project or session identifier, a
prompt, a response, or a tool name.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from .discover import PILOT_AGENTS, SourceRoot, documented_source_roots
from .ingest import discover_candidate_files, enabled_agents
from .store import EventStore


@dataclass(frozen=True, slots=True)
class AgentDiscovery:
    """Factual discovery counts for one pilot agent, never a raw path."""

    agent: str
    enabled: bool
    roots_documented: int
    roots_present: int
    candidate_files: int


@dataclass(frozen=True, slots=True)
class DiscriminatorCount:
    """One (agent, discriminator, kind) count with no source record value."""

    agent: str
    discriminator: str
    kind: str
    count: int


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Aggregate-only ingest health: discovery plus per-discriminator counts."""

    discovery: tuple[AgentDiscovery, ...]
    counts: tuple[DiscriminatorCount, ...]


def build_doctor_report(
    store: EventStore, *, roots: Sequence[SourceRoot] | None = None
) -> DoctorReport:
    """Build one aggregate doctor report from live discovery and store state.

    ``roots`` overrides the scanned roots for tests; the default reuses
    :func:`metermaid.discover.documented_source_roots`. Every discovery
    figure is a count derived from :class:`~metermaid.discover.SourceRoot`
    and :func:`metermaid.ingest.discover_candidate_files`; no root path or
    candidate file path is retained in the returned report.
    """
    resolved_roots = tuple(roots) if roots is not None else documented_source_roots()
    candidates = discover_candidate_files(resolved_roots)
    enabled = enabled_agents()

    roots_by_agent: dict[str, list[SourceRoot]] = {agent: [] for agent in PILOT_AGENTS}
    for root in resolved_roots:
        roots_by_agent.setdefault(root.agent, []).append(root)
    candidate_counts = Counter(candidate.agent for candidate in candidates)

    discovery = tuple(
        AgentDiscovery(
            agent=agent,
            enabled=agent in enabled,
            roots_documented=len(roots_by_agent.get(agent, ())),
            roots_present=sum(
                1 for root in roots_by_agent.get(agent, ()) if root.exists
            ),
            candidate_files=candidate_counts.get(agent, 0),
        )
        for agent in PILOT_AGENTS
    )

    parsed: Counter[tuple[str, str]] = Counter()
    for event in store.events():
        parsed[(event.agent, event.provenance)] += 1

    counts = [
        DiscriminatorCount(
            agent=agent, discriminator=discriminator, kind="parsed", count=count
        )
        for (agent, discriminator), count in parsed.items()
    ]
    counts.extend(
        DiscriminatorCount(
            agent=outcome.agent,
            discriminator=outcome.discriminator,
            kind=outcome.kind,
            count=outcome.count,
        )
        for outcome in store.diagnostics()
    )
    counts.sort(key=lambda row: (row.agent, row.discriminator, row.kind))

    return DoctorReport(discovery=discovery, counts=tuple(counts))
