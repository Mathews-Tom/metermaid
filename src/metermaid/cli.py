"""CLI entry point — argparse, subcommands, dispatches to other modules."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .backfill import backfill
from .compare import provider_comparison
from .consolidate import aggregate, write_aggregate_csv
from .csv_io import read_all_snapshots
from .discover import PILOT_AGENTS
from .doctor import DoctorReport, build_doctor_report
from .hook import handle_claude_hook
from .ingest import IngestSummary, ingest_once
from .models import DEFAULT_INTERVAL, METERMAID_HOME, PID_FILE, SESSIONS_DIR
from .report import filter_rows
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


def _cmd_stop(args: argparse.Namespace) -> None:
    """`watch` is foreground-only in v1: there is no daemon process left
    to signal. This only clears a stale PID file left by a legacy
    daemon run; it never signals a PID, since by the time this runs a
    stale PID may already have been silently reused by an unrelated
    process.
    """
    if PID_FILE.exists():
        PID_FILE.unlink(missing_ok=True)
        console.print("[dim]Removed a stale watcher PID file.[/dim]")
    console.print(
        "[dim]watch runs in the foreground; press Ctrl-C in its terminal "
        "to stop it.[/dim]"
    )


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


def _cmd_hook(args: argparse.Namespace) -> None:
    raw = sys.stdin.read()
    if raw.strip():
        handle_claude_hook(raw, args.data_dir)


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


def _cmd_export(args: argparse.Namespace) -> None:
    from .export import export_dispatch

    rows = read_all_snapshots(args.data_dir)
    filtered = filter_rows(
        rows, window=args.window, session=args.session, provider=args.provider
    )
    fmt = getattr(args, "format", "csv") or "csv"
    out = Path(args.out)
    export_dispatch(filtered, out, fmt)
    console.print(f"Exported [bold]{len(filtered)}[/bold] rows ({fmt}) -> {out}")


def _cmd_backfill(args: argparse.Namespace) -> None:
    r = backfill(
        sessions_dir=args.data_dir,
        since_hours=args.since or 0,
        dry_run=args.dry_run,
        force=args.force,
    )
    mode = " [dim](dry run)[/dim]" if args.dry_run else ""
    console.print(
        f"Backfill{mode}: [bold]{r.found}[/bold] found, "
        f"[green]{r.imported}[/green] imported, "
        f"[dim]{r.already_tracked}[/dim] already tracked, "
        f"[dim]{r.no_data}[/dim] no usage data"
    )


def _cmd_consolidate(args: argparse.Namespace) -> None:
    rows = read_all_snapshots(args.data_dir)
    if args.since:
        rows = [r for r in rows if r.get("timestamp", "") >= args.since]
    if not rows:
        console.print("[yellow]No data found.[/yellow]")
        return
    agg = aggregate(rows, args.window)
    if args.summary:
        provider_comparison(agg)
        return
    out_dir = METERMAID_HOME / "data" / f"{args.window}ly"
    latest = max(r["window"] for r in agg)
    out_path = out_dir / f"{latest}.csv"
    write_aggregate_csv(agg, out_path)
    console.print(f"Wrote [bold]{len(agg)}[/bold] rows -> {out_path}")


def _cmd_migrate(args: argparse.Namespace) -> None:
    import shutil

    old = Path.home() / ".codetrack"
    new = METERMAID_HOME
    if not old.exists():
        console.print("[dim]No ~/.codetrack found — nothing to migrate.[/dim]")
        return
    if new.exists() and any(new.iterdir()):
        console.print("[yellow]~/.metermaid already exists. Skipping.[/yellow]")
        return
    new.mkdir(parents=True, exist_ok=True)
    copied = 0
    for sub in ("sessions", "state"):
        src = old / sub
        if src.exists():
            shutil.copytree(src, new / sub, dirs_exist_ok=True)
            copied += len(list(src.iterdir()))
    console.print(
        f"[green]Migrated[/green] {copied} files from ~/.codetrack -> ~/.metermaid"
    )


def _cmd_mcp(args: argparse.Namespace) -> None:
    from .mcp import serve

    serve(args.data_dir)


def _cmd_heatmap(args: argparse.Namespace) -> None:
    from .csv_io import read_all_snapshots as _read
    from .heatmap import daily_activity, render_heatmap

    rows = _read(args.data_dir)
    activity = daily_activity(rows, days=args.days, metric=args.metric)
    render_heatmap(activity, metric=args.metric, days=args.days)


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

    sub.add_parser("stop").set_defaults(func=_cmd_stop)

    st = sub.add_parser("status")
    st.add_argument("--data-dir", type=Path, dest="v1_data_dir", default=None)
    st.set_defaults(func=_cmd_status)

    doc = sub.add_parser("doctor")
    doc.add_argument("--data-dir", type=Path, dest="v1_data_dir", default=None)
    doc.set_defaults(func=_cmd_doctor)

    sub.add_parser("migrate").set_defaults(func=_cmd_migrate)

    h = sub.add_parser("hook")
    h.add_argument("provider", choices=["claude"])
    h.set_defaults(func=_cmd_hook)

    bf = sub.add_parser("backfill")
    bf.add_argument("--since", type=int, default=0, metavar="HOURS")
    bf.add_argument("--dry-run", action="store_true")
    bf.add_argument("--force", action="store_true")
    bf.set_defaults(func=_cmd_backfill)

    con = sub.add_parser("consolidate")
    con.add_argument("--window", choices=["day", "week", "month"], default="day")
    con.add_argument("--since", type=str, metavar="ISO_DATE")
    con.add_argument("--summary", action="store_true")
    con.set_defaults(func=_cmd_consolidate)

    rep = sub.add_parser("report")
    rep.add_argument("--data-dir", type=Path, dest="v1_data_dir", default=None)
    rep.add_argument("--since", type=_parse_range_bound, default=None)
    rep.add_argument("--until", type=_parse_range_bound, default=None)
    rep.add_argument("--agent", choices=PILOT_AGENTS, default=None)
    rep.add_argument("--model", default=None)
    rep.add_argument("--project-key", default=None)
    rep.set_defaults(func=_cmd_report)

    exp = sub.add_parser("export")
    exp.add_argument("--window")
    exp.add_argument("--session")
    exp.add_argument("--provider", choices=["claude", "codex"])
    exp.add_argument(
        "--format",
        choices=["csv", "json", "markdown", "html", "otlp"],
        default="csv",
    )
    exp.add_argument("--out", default="metermaid_export.csv")
    exp.set_defaults(func=_cmd_export)

    sub.add_parser("mcp").set_defaults(func=_cmd_mcp)

    hm = sub.add_parser("heatmap")
    hm.add_argument("--metric", choices=["cost", "tokens", "sessions"], default="cost")
    hm.add_argument("--days", type=int, default=365)
    hm.set_defaults(func=_cmd_heatmap)

    args = p.parse_args()
    args.func(args)
