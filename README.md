# codetrack

Background usage tracker for Claude Code and Codex CLI sessions.

Polls active session directories, extracts token usage, context window state, cost data, and timing from JSONL transcripts, then writes deduplicated snapshots to per-session CSV files. One file per session — no shared read-modify-write, safe across multiple terminals/tabs/windows. Works on macOS, Linux, Windows, and WSL. Zero dependencies.

## Install

```bash
uv tool install codetrack-cli
```

Or from source:

```bash
uv tool install -e .
```

Requires Python 3.11+. No dependencies beyond stdlib.

## Quick start

```bash
# Import all existing sessions from disk
codetrack backfill

# Start watching for new activity (background)
codetrack watch --daemon

# See what's happening
codetrack status
codetrack report
```

## Usage

### Watch

Polls Claude Code and Codex CLI session directories for changes. Both providers are tracked automatically — no configuration needed.

```bash
codetrack watch                  # foreground (Ctrl+C to stop)
codetrack watch --daemon         # background (writes PID file)
codetrack watch --interval 5     # custom poll interval (seconds)
codetrack stop                   # stop background watcher
codetrack status                 # watcher state + active sessions
```

### Backfill

Import historical sessions already on disk. Idempotent — skips sessions that are already tracked. Uses each file's modification time as the snapshot timestamp, so historical data appears in the correct report windows.

```bash
codetrack backfill               # all sessions
codetrack backfill --since 168   # last 7 days (168 hours)
codetrack backfill --dry-run     # preview without writing
```

### Report

```bash
codetrack report                 # all time
codetrack report --window 7d     # last 7 days
codetrack report --window 5h     # last 5 hours
codetrack report --provider claude
codetrack report --session abc123
```

### Export

```bash
codetrack export --window 30d --out export.csv
codetrack export --provider codex --out codex.csv
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

| Field                              | Source                                    |
| ---------------------------------- | ----------------------------------------- |
| `tokens_in`, `tokens_out`          | Cumulative input/output tokens            |
| `cache_read`, `cache_write`        | Prompt cache usage                        |
| `cost_usd`                         | `costUSD` from transcript (Claude Code)   |
| `ctx_pct`, `ctx_tokens`, `ctx_max` | Context window utilization                |
| `wall_sec`                         | Wall clock time (first to last timestamp) |
| `api_sec`                          | API latency (statusLine hook only)        |
| `diff_add`, `diff_del`             | Lines added/removed (statusLine hook only)|
| `model`, `provider`, `session_id`  | Session identification                    |

## Storage

Per-session CSV files at `~/.codetrack/sessions/{provider}_{session_id}.csv`. Each file is append-only, written by exactly one process. Reports scan all session files and merge on read.

```
~/.codetrack/
  sessions/
    claude_a1b2c3d4e5f6.csv
    claude_f7e8d9c0b1a2.csv
    codex_session1234.csv
  codetrack.pid
  codetrack.log
```

## Cost tracking

codetrack captures `costUSD` when present in Claude Code transcripts but does not estimate cost from tokens. For detailed cost breakdowns across providers, use [ccusage](https://ccusage.com).

## How it works

- **Filesystem polling**: Scans session directories every 10 seconds (configurable). No filesystem watchers or inotify — pure polling for maximum portability, including WSL where inotify doesn't work on Windows-side paths.
- **Deduplication**: Two-layer dedup — mtime check skips unchanged files, content hash (provider + session + token counts) prevents duplicate snapshots when data hasn't changed between polls.
- **Per-session isolation**: No shared read-modify-write. Multiple watchers across terminals are safe because each session maps to exactly one file.
- **Cross-platform daemon**: `--daemon` uses `fork` on Unix and `subprocess.Popen` with `DETACHED_PROCESS` on Windows.

## Advanced: statusLine hook

The watcher gets all data from JSONL transcripts. For additional fields (`api_sec`, `diff_add`, `diff_del`), you can manually configure Claude Code's statusLine to pipe through codetrack:

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "codetrack hook claude"
  }
}
```
