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
# Import session activity from disk
metermaid ingest

# Start watching for new activity
metermaid watch

# See what's happening
metermaid status
metermaid report
```

## Usage

### Watch

Polls Claude Code and Codex CLI session directories for changes. Both providers are tracked automatically — no configuration needed.

```bash
metermaid watch                  # foreground (Ctrl+C to stop)
metermaid watch --interval 5     # custom poll interval (seconds)
metermaid status                 # store + discovery summary
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

Export an aggregated, privacy-safe usage summary (grouped by agent) as JSON. Prints the field list and a sample row before writing.

```bash
metermaid export                    # writes metermaid_export.json
metermaid export --out usage.json   # custom output path
```

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
