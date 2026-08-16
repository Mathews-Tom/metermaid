"""CLI entry point — argparse, subcommands, dispatches to other modules."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .discover import PILOT_AGENTS
from .doctor import DoctorReport, build_doctor_report
from .export_v1 import build_export, export_preview, write_export
from .ingest import IngestSummary, ingest_once
from .legacy_v1 import (
    LegacyHistoryReport,
    LegacyImportSummary,
    LegacyProviderAggregate,
    build_legacy_report,
    import_legacy_snapshots,
)
from .models import DEFAULT_INTERVAL, SESSIONS_DIR
from .report_v1 import GroupAggregate, ObservedReport, ReportFilter, build_report
from .state import load_or_create_secret, resolve_state_paths
from .store import EventStore

console = Console()


def _resolve_v1_data_dir(args: argparse.Namespace) -> Path | None:
    """Resolve the effective v1 state root.

    A subcommand-level ``--data-dir`` (``dest="v1_data_dir"``) always
    wins. Otherwise, an explicit top-level ``--data-dir`` given before
    the subcommand — a value that differs from the legacy
    ``SESSIONS_DIR`` default meant only for the CSV-era commands — is
    honored too, so ``metermaid --data-dir X ingest`` keeps working.
    When neither is given, this returns ``None`` so
    ``resolve_state_paths`` applies its own bare v1 default instead of
    nesting the v1 store under the legacy sessions subdirectory.
    """
    override = getattr(args, "v1_data_dir", None)
    if override is not None:
        return Path(override)
    top_level = getattr(args, "data_dir", None)
    if top_level is not None and top_level != SESSIONS_DIR:
        return Path(top_level)
    return None


def _open_v1_store(args: argparse.Namespace) -> tuple[EventStore, bytes]:
    """Resolve the v1 state root and open its store with a loaded secret."""
    paths = resolve_state_paths(_resolve_v1_data_dir(args))
    secret = load_or_create_secret(paths)
    store = EventStore(paths.database)
    store.initialize()
    return store, secret


def _print_ingest_summary(summary: IngestSummary) -> None:
    console.print(
        f"Ingest: [bold]{summary.files_read}[/bold] files read, "
        f"[green]{summary.events_inserted}[/green] events inserted, "
        f"[dim]{summary.diagnostics_recorded}[/dim] diagnostics recorded"
    )


def _cmd_ingest(args: argparse.Namespace) -> None:
    store, secret = _open_v1_store(args)
    _print_ingest_summary(ingest_once(store, secret))


def _positive_interval(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid interval: {value!r}") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            "--interval must be a positive number of seconds"
        )
    return parsed


def _parse_range_bound(value: str) -> datetime:
    """Parse an ISO-8601 timestamp into an aware-UTC report-range bound."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _watch_loop(
    store: EventStore,
    secret: bytes,
    interval: int,
    *,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Foreground incremental-ingest polling loop; stops on ``KeyboardInterrupt``.

    ``sleep`` is resolved at call time (via ``time.sleep`` when ``None``)
    rather than bound as a default at function-definition time, so a
    test can intercept the real ``time.sleep`` through ``time`` module
    patching even when this is invoked indirectly through ``_cmd_watch``.
    """
    wait = sleep if sleep is not None else time.sleep
    try:
        while True:
            _print_ingest_summary(ingest_once(store, secret))
            wait(interval)
    except KeyboardInterrupt:
        console.print("[dim]Stopped[/dim]")


def _cmd_watch(args: argparse.Namespace) -> None:
    store, secret = _open_v1_store(args)
    console.print(f"[bold]metermaid[/bold] watching (interval={args.interval}s)")
    _watch_loop(store, secret, args.interval)


def _discovery_table(report: DoctorReport) -> Table:
    t = Table(title="Source discovery", box=None)
    t.add_column("Agent", style="cyan")
    t.add_column("Enabled")
    t.add_column("Roots present", justify="right")
    for agent in report.discovery:
        t.add_row(
            agent.agent,
            "[green]yes[/green]" if agent.enabled else "[yellow]no[/yellow]",
            f"{agent.roots_present}/{agent.roots_documented}",
        )
    return t


def _cmd_status(args: argparse.Namespace) -> None:
    store, _secret = _open_v1_store(args)
    events = store.events()
    diagnostics = store.diagnostics()
    sessions = {event.source_session_id for event in events}
    diagnostic_total = sum(outcome.count for outcome in diagnostics)

    console.print(
        f"Store: [bold]{len(events)}[/bold] events, "
        f"[cyan]{len(sessions)}[/cyan] sessions, "
        f"[dim]{diagnostic_total}[/dim] diagnostics"
    )
    console.print(_discovery_table(build_doctor_report(store)))


def _cmd_doctor(args: argparse.Namespace) -> None:
    store, _secret = _open_v1_store(args)
    report = build_doctor_report(store)

    console.print(_discovery_table(report))

    outcomes = Table(title="Parse outcomes", box=None)
    outcomes.add_column("Agent", style="cyan")
    outcomes.add_column("Discriminator")
    outcomes.add_column("Kind")
    outcomes.add_column("Count", justify="right")
    for row in report.counts:
        outcomes.add_row(row.agent, row.discriminator, row.kind, str(row.count))
    if not report.counts:
        outcomes.add_row("[dim]none[/dim]", "", "", "")
    console.print(outcomes)


def _format_optional_int(value: int | None) -> str:
    return str(value) if value is not None else "unavailable"


def _format_optional_cost(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "unavailable"


def _report_group_table(
    title: str, key_header: str, rows: tuple[GroupAggregate, ...]
) -> Table:
    t = Table(title=title, box=None)
    t.add_column(key_header, style="cyan")
    t.add_column("Events", justify="right")
    t.add_column("Sessions", justify="right")
    t.add_column("Tokens in", justify="right")
    t.add_column("Tokens out", justify="right")
    t.add_column("Cache read", justify="right")
    t.add_column("Cache write", justify="right")
    t.add_column("Reasoning", justify="right")
    t.add_column("Cost (USD)", justify="right")
    for row in rows:
        t.add_row(
            row.key,
            str(row.event_count),
            str(row.session_count),
            _format_optional_int(row.tokens.tokens_in),
            _format_optional_int(row.tokens.tokens_out),
            _format_optional_int(row.tokens.cache_read),
            _format_optional_int(row.tokens.cache_write),
            _format_optional_int(row.tokens.reasoning_tokens),
            _format_optional_cost(row.provider_cost_usd),
        )
    if not rows:
        t.add_row("[dim]none[/dim]", "", "", "", "", "", "", "", "")
    return t


def _print_observed_report(observed: ObservedReport) -> None:
    console.print(
        f"Observed: [bold]{observed.event_count}[/bold] events, "
        f"[cyan]{observed.session_count}[/cyan] sessions"
    )
    console.print(
        f"Totals: in={_format_optional_int(observed.tokens.tokens_in)} "
        f"out={_format_optional_int(observed.tokens.tokens_out)} "
        f"cache_read={_format_optional_int(observed.tokens.cache_read)} "
        f"cache_write={_format_optional_int(observed.tokens.cache_write)} "
        f"reasoning={_format_optional_int(observed.tokens.reasoning_tokens)} "
        f"cost_usd={_format_optional_cost(observed.provider_cost_usd)}"
    )
    console.print(_report_group_table("By agent", "Agent", observed.by_agent))
    console.print(_report_group_table("By model", "Model", observed.by_model))
    console.print(
        _report_group_table("By project key", "Project key", observed.by_project_key)
    )


def _legacy_provider_table(rows: tuple[LegacyProviderAggregate, ...]) -> Table:
    t = Table(title="By provider", box=None)
    t.add_column("Provider", style="cyan")
    t.add_column("Rows", justify="right")
    t.add_column("Tokens in", justify="right")
    t.add_column("Tokens out", justify="right")
    t.add_column("Cache read", justify="right")
    t.add_column("Cache write", justify="right")
    t.add_column("Cost (USD)", justify="right")
    for row in rows:
        t.add_row(
            row.provider,
            str(row.row_count),
            str(row.totals.tokens_in),
            str(row.totals.tokens_out),
            str(row.totals.cache_read),
            str(row.totals.cache_write),
            f"{row.totals.cost_usd:.4f}",
        )
    if not rows:
        t.add_row("[dim]none[/dim]", "", "", "", "", "", "")
    return t


def _print_legacy_history(legacy: LegacyHistoryReport) -> None:
    """Render imported v0.2 history in its own, clearly marked section.

    Never appears inside :func:`_print_observed_report`'s totals or
    tables: legacy rows are never summed or grouped together with
    current normalized events.
    """
    console.print(
        f"[bold]Legacy history (imported v0.2 snapshots):[/bold] "
        f"[bold]{legacy.row_count}[/bold] rows"
    )
    console.print(
        f"Legacy totals: in={legacy.totals.tokens_in} "
        f"out={legacy.totals.tokens_out} "
        f"cache_read={legacy.totals.cache_read} "
        f"cache_write={legacy.totals.cache_write} "
        f"cost_usd={legacy.totals.cost_usd:.4f}"
    )
    console.print(_legacy_provider_table(legacy.by_provider))


def _print_legacy_import_summary(summary: LegacyImportSummary) -> None:
    console.print(
        f"Legacy import: scanned=[bold]{summary.files_scanned}[/bold] files, "
        f"imported=[bold]{summary.files_imported}[/bold], "
        f"unsupported_header={summary.files_unsupported_header}, "
        f"malformed={summary.files_malformed}"
    )
    console.print(
        f"Legacy rows: inserted=[bold]{summary.rows_inserted}[/bold], "
        f"duplicate={summary.rows_duplicate}"
    )


def _cmd_import_legacy(args: argparse.Namespace) -> None:
    store, secret = _open_v1_store(args)
    summary = import_legacy_snapshots(store, secret, args.legacy_dir)
    _print_legacy_import_summary(summary)


def _cmd_report(args: argparse.Namespace) -> None:
    store, _secret = _open_v1_store(args)
    filter_ = ReportFilter(
        since=args.since,
        until=args.until,
        agent=args.agent,
        model=args.model,
        project_key=args.project_key,
    )
    observed = build_report(store.events(), filter_)
    _print_observed_report(observed)
    _print_legacy_history(build_legacy_report(store.legacy_snapshots()))


def _cmd_export(args: argparse.Namespace) -> None:
    store, _secret = _open_v1_store(args)
    document = build_export(store.events())
    console.print(export_preview(document))
    out = Path(args.out)
    write_export(document, out)
    console.print(f"Exported [bold]{len(document.rows)}[/bold] rows -> {out}")


def main() -> None:
    p = argparse.ArgumentParser(
        prog="metermaid",
        description="Session metrics watcher for Claude Code & Codex CLI",
    )
    p.add_argument("--data-dir", type=Path, default=SESSIONS_DIR)
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest")
    ing.add_argument("--data-dir", type=Path, dest="v1_data_dir", default=None)
    ing.set_defaults(func=_cmd_ingest)

    w = sub.add_parser("watch")
    w.add_argument("--data-dir", type=Path, dest="v1_data_dir", default=None)
    w.add_argument("--interval", type=_positive_interval, default=DEFAULT_INTERVAL)
    w.set_defaults(func=_cmd_watch)

    st = sub.add_parser("status")
    st.add_argument("--data-dir", type=Path, dest="v1_data_dir", default=None)
    st.set_defaults(func=_cmd_status)

    doc = sub.add_parser("doctor")
    doc.add_argument("--data-dir", type=Path, dest="v1_data_dir", default=None)
    doc.set_defaults(func=_cmd_doctor)

    rep = sub.add_parser("report")
    rep.add_argument("--data-dir", type=Path, dest="v1_data_dir", default=None)
    rep.add_argument("--since", type=_parse_range_bound, default=None)
    rep.add_argument("--until", type=_parse_range_bound, default=None)
    rep.add_argument("--agent", choices=PILOT_AGENTS, default=None)
    rep.add_argument("--model", default=None)
    rep.add_argument("--project-key", default=None)
    rep.set_defaults(func=_cmd_report)

    exp = sub.add_parser("export")
    exp.add_argument("--data-dir", type=Path, dest="v1_data_dir", default=None)
    exp.add_argument("--out", default="metermaid_export.json")
    exp.set_defaults(func=_cmd_export)

    imp = sub.add_parser("import-legacy")
    imp.add_argument("--data-dir", type=Path, dest="v1_data_dir", default=None)
    imp.add_argument("legacy_dir", type=Path, nargs="?", default=SESSIONS_DIR)
    imp.set_defaults(func=_cmd_import_legacy)

    args = p.parse_args()
    args.func(args)
