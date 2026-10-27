"""Read-only discovery and transient rendering for the opt-in developer advisor."""

from __future__ import annotations

import argparse
import secrets
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from .adapters import RecordContext
from .discover import SourceRoot, documented_source_roots
from .domain import ParseOutcome
from .ingest import _ADAPTERS, _decode_line, discover_candidate_files
from .schema_advisor import AdvisorError, group_records, load_config
from .schema_advisor_client import Advice, advise_groups
from .state import opaque_identifier


def collect_unsupported(
    roots: Sequence[SourceRoot],
) -> Iterator[tuple[str, str, Mapping[str, object]]]:
    """Select unsupported complete records without opening Metermaid state.

    An ephemeral secret supplies opaque adapter context only for this scan.
    Neither that context nor any parsed event is retained in an advisory group.
    """
    secret = secrets.token_bytes(32)
    for candidate in discover_candidate_files(roots):
        adapter = _ADAPTERS[candidate.agent]
        session = opaque_identifier(secret, "advisor-session", str(candidate.path))
        project = opaque_identifier(
            secret, "advisor-project", str(candidate.path.parent)
        )
        with candidate.path.open("rb") as source:
            offset = 0
            for line in source:
                if not line.endswith(b"\n"):
                    break
                context = RecordContext(
                    source_session_id=session, project_key=project, byte_start=offset
                )
                offset += len(line)
                record = _decode_line(candidate.agent, line)
                if record is None or isinstance(record, ParseOutcome):
                    continue
                outcome = adapter.parse(record, context=context, secret=secret)
                if isinstance(outcome, ParseOutcome) and outcome.kind == "unsupported":
                    yield candidate.agent, outcome.discriminator, record


def render_advice(advice: Sequence[Advice], output: TextIO) -> None:
    """Print only the allowlisted aggregate columns, never model rationale."""
    print("signature\tagent\tdiscriminator\tcount\tcategory", file=output)
    for item in advice:
        group = item.group
        print(
            f"{group.signature_id}\t{group.agent}\t{group.discriminator}"
            f"\t{group.count}\t{item.category}",
            file=output,
        )


def run(config_path: Path | None, output: TextIO) -> int:
    """Validate opt-in before discovery, then classify each aggregate once."""
    config = load_config(config_path)
    groups = group_records(collect_unsupported(documented_source_roots()))
    advice = advise_groups(config, groups)
    render_advice(advice, output)
    return int(any(item.failed for item in advice))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the developer script with a value-free, nonzero failure boundary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="explicit advisor opt-in TOML file")
    args = parser.parse_args(argv)
    try:
        return run(args.config, sys.stdout)
    except (AdvisorError, OSError, ValueError, TypeError, RecursionError):
        print("needs-human-review", file=sys.stderr)
        return 1
