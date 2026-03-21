# metermaid

Background usage tracker for Claude Code and Codex CLI sessions.

Polls active session directories, extracts token usage, context window state, cost data, and timing from JSONL transcripts, then writes deduplicated snapshots to per-session CSV files. One file per session — no shared read-modify-write, safe across multiple terminals/tabs/windows. Works on macOS, Linux, Windows, and WSL.

## Install

```bash
uv tool install metermaid-cli
```

Or from source:

```bash
uv tool install -e .
```

Requires Python 3.11+. Single dependency: `rich`.

## Quick start

```bash
# Import all existing sessions from disk
metermaid backfill

# Start watching for new activity (background)
metermaid watch --daemon

# See what's happening
metermaid status
metermaid report
```

## Usage

### Watch

Polls Claude Code and Codex CLI session directories for changes. Both providers are tracked automatically — no configuration needed.

```bash
metermaid watch                  # foreground (Ctrl+C to stop)
metermaid watch --daemon         # background (writes PID file)
metermaid watch --interval 5     # custom poll interval (seconds)
metermaid stop                   # stop background watcher
metermaid status                 # watcher state + active sessions
```

### Backfill

Import historical sessions already on disk. Idempotent — skips sessions that are already tracked. Uses each file's modification time as the snapshot timestamp, so historical data appears in the correct report windows.

```bash
metermaid backfill               # all sessions
metermaid backfill --since 168   # last 7 days (168 hours)
metermaid backfill --dry-run     # preview without writing
```

### Report

Shows session summary with sparkline trends, cache hit rate, cost-per-line, week-over-week comparison, cost windows, budget gauge (if configured), and actionable nudges.

```bash
metermaid report                 # all time
metermaid report --window 7d     # last 7 days
metermaid report --window 5h     # last 5 hours
metermaid report --provider claude
metermaid report --session abc123
```

Report includes:

- Token and cost totals with 7-day Unicode sparkline trends (`▁▂▃▄▅▆▇█`)
- Cache hit rate color-coded by threshold (green >70%, yellow 40–70%, red <40%)
- Cost per line changed (`total_cost / lines_changed`)
- Week-over-week comparison with delta arrows
- Cost windows (5h / 7d / 30d)
- Budget gauge and end-of-month forecast (when configured)
- Actionable nudges: cache hit drops, cost spikes, context pressure warnings

### Export

Export session data in multiple formats.

```bash
metermaid export --window 30d --out export.csv
metermaid export --format json --out report.json
metermaid export --format markdown --out report.md
metermaid export --format html --out report.html
metermaid export --format otlp --out metrics.json
```

| Format     | Description                                            |
| ---------- | ------------------------------------------------------ |
| `csv`      | Raw snapshot rows (default)                            |
| `json`     | Typed fields (numerics, not all strings)               |
| `markdown` | Summary stats + session table for PR comments or Slack |
| `html`     | Standalone report with inline CSS                      |
| `otlp`     | OpenTelemetry-compatible JSON for Prometheus/Grafana   |

### Heatmap

GitHub-style contributions calendar in the terminal.

```bash
metermaid heatmap                        # cost by default, last 365 days
metermaid heatmap --metric tokens        # token volume
metermaid heatmap --metric sessions      # session count
metermaid heatmap --days 90              # last 90 days
```

### Consolidate

Aggregate data into time-windowed summaries with derived metrics (cost/hour, token efficiency, cost/kTok output).

```bash
metermaid consolidate                    # daily aggregates
metermaid consolidate --window week      # weekly
metermaid consolidate --window month     # monthly
metermaid consolidate --summary          # Claude vs Codex comparison
```

### MCP server

Expose usage stats as an MCP (Model Context Protocol) server over stdin/stdout. Allows AI assistants to query your usage data programmatically.

```bash
metermaid mcp
```

Available tools:

- `get_usage_summary` — aggregate stats (sessions, tokens, cost, cache hit rate)
- `get_session_list` — per-session details with optional time window filter
- `get_cost_windows` — cost totals for 5h, 7d, and 30d windows

### Migrate

Copy data from a previous `~/.codetrack/` installation.

```bash
metermaid migrate
```

## Budget tracking

Create `~/.metermaid/config.toml` to enable budget monitoring in reports:

```toml
[budget]
monthly_usd = 150.00
alert_thresholds = [50, 75, 90, 100]

[budget.provider]
claude = 100.00
codex = 50.00
```

Reports will show a progress bar, end-of-month cost forecast, and threshold alerts when spending crosses configured percentages.

## Session discovery

Auto-discovers sessions with no configuration:

| Provider             | Paths scanned                                                 |
| -------------------- | ------------------------------------------------------------- |
| Claude Code          | `~/.config/claude/projects/<hash>/<session>.jsonl` (v1.0.30+) |
| Claude Code (legacy) | `~/.claude/projects/<hash>/<session>.jsonl`                   |
| Codex CLI            | `~/.codex/sessions/` (or `$CODEX_HOME/sessions/`)             |
| WSL                  | All of the above under `/mnt/c/Users/<name>/`                 |

## Data captured

Each snapshot records:

| Field                              | Source                                     |
| ---------------------------------- | ------------------------------------------ |
| `tokens_in`, `tokens_out`          | Cumulative input/output tokens             |
| `cache_read`, `cache_write`        | Prompt cache usage                         |
| `cost_usd`                         | `costUSD` from transcript (Claude Code)    |
| `ctx_pct`, `ctx_tokens`, `ctx_max` | Context window utilization                 |
| `wall_sec`                         | Wall clock time (first to last timestamp)  |
| `api_sec`                          | API latency (statusLine hook only)         |
| `diff_add`, `diff_del`             | Lines added/removed (statusLine hook only) |
| `model`, `provider`, `session_id`  | Session identification                     |

## Storage

Per-session CSV files at `~/.metermaid/sessions/{provider}_{session_id}.csv`. Each file is append-only, written by exactly one process. Reports scan all session files and merge on read.

```
~/.metermaid/
  sessions/
    claude_a1b2c3d4e5f6.csv
    codex_session1234.csv
  state/
    claude_a1b2c3d4e5f6.state
  config.toml
  metermaid.pid
  metermaid.log
```

## How it works

- **Filesystem polling**: Scans session directories every 10 seconds (configurable). No filesystem watchers or inotify — pure polling for maximum portability, including WSL where inotify doesn't work on Windows-side paths.
- **Deduplication**: Two-layer dedup — mtime check skips unchanged files, content hash (provider + session + token counts) prevents duplicate snapshots when data hasn't changed between polls.
- **Per-session isolation**: No shared read-modify-write. Multiple watchers across terminals are safe because each session maps to exactly one file.
- **Cross-platform daemon**: `--daemon` uses `fork` on Unix and `subprocess.Popen` with `DETACHED_PROCESS` on Windows.

## Advanced: statusLine hook

The watcher gets all data from JSONL transcripts. For additional fields (`api_sec`, `diff_add`, `diff_del`), configure Claude Code's statusLine to pipe through metermaid:

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "metermaid hook claude"
  }
}
```
