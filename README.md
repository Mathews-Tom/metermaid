# metermaid

Background usage tracker for Claude Code, Codex CLI, Pi, and OMP sessions.

Polls documented session directories, incrementally reads new JSONL records, and normalizes each one into a text-free usage event stored in a local SQLite database. No prompt, tool argument, raw session identifier, or source file path is ever persisted, exported, or printed — every identifier is an opaque HMAC digest derived from a machine-local secret. Runs on macOS, Linux, and WSL; the watcher also scans the Windows-side home under WSL.

This is a personal dogfood pilot, not a published package: install it from a source checkout.

## Install

```bash
git clone https://github.com/Mathews-Tom/metermaid.git
cd metermaid
uv tool install .
```

Or run it without installing, from inside the checkout:

```bash
uv run metermaid ingest
```

Or build and install the wheel directly (what the clean-install smoke test in `tests/test_install_smoke.py` exercises):

```bash
uv build --wheel
uv tool install dist/metermaid_cli-*.whl
```

Requires Python 3.11+. Single runtime dependency: `rich`.

## Quick start

```bash
# Import session activity observed since the store was created
metermaid ingest

# Keep importing in the foreground (Ctrl+C to stop)
metermaid watch

# See what's happening
metermaid status
metermaid doctor
metermaid report
```

## Commands

`metermaid` supports exactly seven subcommands. Every subcommand accepts `--data-dir PATH` to point at an alternate state root (see [Storage](#storage)); `--data-dir` may also be given once before the subcommand.

| Command | Flags | Does |
| --- | --- | --- |
| `ingest` | `--data-dir` | Runs one incremental ingest pass over every documented source and exits. |
| `watch` | `--data-dir`, `--interval` (seconds, default `10`) | Runs `ingest` in a foreground loop until interrupted with Ctrl+C. |
| `status` | `--data-dir` | Prints aggregate event/session/diagnostic counts plus the source-discovery table. |
| `doctor` | `--data-dir` | Prints the source-discovery table plus per-agent, per-discriminator parse-outcome counts (`parsed` / `malformed` / `unsupported`). |
| `report` | `--data-dir`, `--since`, `--until` (ISO-8601), `--agent`, `--model`, `--project-key` | Prints observed-event totals and by-agent/by-model/by-project-key breakdowns, plus imported legacy history in its own section. |
| `export` | `--data-dir`, `--out` (default `metermaid_export.json`) | Writes the restricted aggregate JSON export; prints the field list and one sample row before writing. |
| `import-legacy` | `--data-dir`, optional positional `LEGACY_DIR` (default `~/.metermaid/sessions`) | Explicitly imports v0.2 CSV history — see [Legacy v0.2 import](#legacy-v02-import). |

## Session discovery

`doctor`/`status` report discovery for four pilot agents. A root existing on disk is only a filesystem fact; an agent is `enabled` only when it also has a registered, fixture-backed adapter — currently all four.

| Agent | Roots scanned |
| --- | --- |
| Claude Code | `<home>/.config/claude/projects/**/*.jsonl` (current) and `<home>/.claude/projects/**/*.jsonl` (legacy) |
| Codex CLI | `$CODEX_HOME/sessions/**/rollout-*.jsonl` (if `CODEX_HOME` is set) and `<home>/.codex/sessions/**/rollout-*.jsonl` |
| Pi | `<home>/.pi/agent/sessions/**/*.jsonl` |
| OMP | `<home>/.omp/agent/sessions/**/*.jsonl` |

Every `<home>` is scanned once per detected home directory: the native home, plus the Windows-side home under WSL.

## Data captured

Each normalized event may carry:

| Field | Meaning |
| --- | --- |
| `agent` | One of `claude-code`, `codex`, `pi`, `omp`. |
| `model` | Model label, when the source record carries one; otherwise unavailable. |
| `tokens_in`, `tokens_out` | Per-record input/output token counts, when present. |
| `cache_read`, `cache_write` | Prompt-cache token counts, when present. |
| `reasoning_tokens` | Reasoning-token count, when the source reports one. |
| `provider_cost_usd` | Provider-reported cost, when present. |
| `source_session_id`, `project_key` | Opaque HMAC digests — never the raw session id or path. |
| `occurred_at` | The record's own timestamp, normalized to UTC. |

A missing counter is reported as `unavailable`, never invented as zero. Metermaid does not track context-window percentage, wall-clock or API latency, diff line counts, raw file paths, prompts, or tool arguments — only the counters above.

## Storage

Default state root: `~/.metermaid/` (override with `--data-dir`):

```
~/.metermaid/
  metermaid.sqlite3   # normalized events, watermarks, diagnostics, imported legacy rows
  metermaid.secret    # machine-local HMAC secret (0600), created on first run
```

`metermaid.secret` derives every opaque identifier the database stores; nothing here is ever a raw path, prompt, or session id. Reports and exports read only from this local database — no network calls, no shared state across machines.

## Legacy v0.2 import

An older v0.2 install may have left per-session CSV files at `~/.metermaid/sessions/*.csv`. Metermaid v1 never reads them automatically — `ingest`/`watch` only ever produce normalized events from live sources. Importing legacy history is always explicit:

```bash
metermaid import-legacy                    # reads ~/.metermaid/sessions
metermaid import-legacy /path/to/csvs      # or an explicit directory
```

The importer is read-only and narrow:

- Only a CSV whose header exactly matches the original v0.2 header is imported; any other header skips the whole file.
- Only eight columns are read: `timestamp`, `provider`, `model`, `tokens_in`, `tokens_out`, `cache_read`, `cache_write`, `cost_usd`. Raw path, session id, deltas, sidechain fields, and context/timing columns are never read.
- A file with any malformed numeric cell is rejected in full — no partial import.
- Imports are idempotent: re-running over the same files never duplicates rows.
- Imported rows are stored and reported as `LegacySnapshot` history, entirely separate from current observed events — `report` never merges legacy totals into the observed totals.
- The source CSV files are opened read-only and are never modified, moved, or deleted.

## Uninstall and data retention

```bash
uv tool uninstall metermaid-cli
```

removes the installed CLI only. It never touches `~/.metermaid`. To remove all local data as well:

```bash
rm -rf ~/.metermaid
```

(or your `--data-dir`, if you used one).

## Development

```bash
uv sync --all-groups --locked --python 3.11
uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest
```
