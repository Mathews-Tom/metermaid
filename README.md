# metermaid

Private, local usage accounting for Claude Code, Codex CLI, Pi, and OMP.

## Purpose and status

Metermaid consolidates the usage records already on your machine into one cross-agent ledger: which agents and models consume tokens, how much usage is input/output/cache/reasoning, and what costs the sources report. Reports support time, agent, model, and opaque project-key filters.

The approved v1 personal-pilot milestones are complete. Continued use does not require another evidence campaign, an always-running watcher, or daily review acknowledgements. Install the current implementation from a source checkout; completion of the pilot is not a new public release.

Metermaid reads complete JSONL records from documented session directories, converts supported usage shapes into text-free events, and stores them in local SQLite. Source session and project identifiers are HMAC-derived opaque keys. Prompt/response content, tool arguments/results, and raw source paths are excluded from normalized storage and restricted exports. The seven normal CLI commands make no service calls. Runs on macOS, Linux, and WSL; discovery also checks the Windows-side home under WSL.

This is not a productivity score, an exact billing system, a quota predictor, or an automatic spending advisor. Read [Reporting limits](#reporting-limits) before treating totals as authoritative.

## Install

```bash
git clone https://github.com/Mathews-Tom/metermaid.git
cd metermaid
uv tool install .
```

After updating the checkout, refresh an existing installed tool with `uv tool install --force .`. `uv run metermaid ...` uses the checkout; a separately installed `metermaid` command does not automatically follow local edits.

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

## Everyday use

The simplest workflow is an on-demand ingest followed by a report:

```bash
metermaid ingest
metermaid report

# Narrow the stored observations by date and agent
metermaid report --since 2026-10-21 --agent claude-code

# Inspect parser outcomes or overall store/evidence counts
metermaid doctor
metermaid status
```

From the checkout, prefix each command with `uv run`, for example `uv run metermaid report`.

`ingest` reads one bounded batch per discovered file, then exits. A first run can import older records still present on disk, not just activity since installation. Repeat `ingest` to catch up with a large backlog. Subsequent passes resume from stored complete-line offsets; they do not intentionally recount unchanged records from the same source generation. An incomplete final line waits until it is newline-terminated.

A single JSONL record, including its terminating newline, must fit in the 16 MiB per-file read batch. A larger record leaves that file's complete-line offset stuck before the record; repeated passes cannot reach it or later records in that file. This is an ingestion limit, not a backlog that additional polling resolves.

`report`, `status`, `doctor`, and `export` do not ingest new source records. Refresh with `ingest` first when you need current observations. Source files deleted before collection cannot be recovered by Metermaid.

Continuous collection is optional:

```bash
metermaid watch --interval 10
```

Keep it in a foreground terminal and stop with **Ctrl+C**. Each poll performs ingestion, then waits the interval; ingest time is additional. There is no `stop` command, daemon, or service-manager installation. Stopping leaves the database and source files intact and does not affect your coding agents.

## Commands

`metermaid` supports exactly seven subcommands. Every subcommand accepts `--data-dir PATH` to point at an alternate state root (see [Storage](#storage)); `--data-dir` may also be given once before the subcommand.

| Command | Flags | Does |
| --- | --- | --- |
| `ingest` | `--data-dir` | Runs one incremental ingest pass over every documented source and exits. |
| `watch` | `--data-dir`, `--interval` (seconds, default `10`) | Runs `ingest` in a foreground loop until interrupted with Ctrl+C. |
| `status` | `--data-dir` | Prints aggregate event/session/diagnostic counts, source discovery, rolling dogfood coverage, and completed-day evidence criteria. It is not a live process-health check. |
| `doctor` | `--data-dir`, `--acknowledge` | Prints source discovery and per-agent, per-discriminator parse-outcome counts (`parsed` / `malformed` / `unsupported`). The optional flag records that the displayed state was reviewed. |
| `report` | `--data-dir`, `--since`, `--until` (ISO-8601), `--agent`, `--model`, `--project-key` | Prints observed-event totals and by-agent/by-model/by-project-key breakdowns, plus imported legacy history in its own section. |
| `export` | `--data-dir`, `--out` (default `metermaid_export.json`) | Writes the restricted aggregate JSON export; prints the field list and one sample row before writing. |
| `import-legacy` | `--data-dir`, optional positional `LEGACY_DIR` (default `~/.metermaid/sessions`) | Explicitly imports v0.2 CSV history — see [Legacy v0.2 import](#legacy-v02-import). |

## Progress feedback

Long-running operations show transient Rich spinners with the current stage, including store initialization, ingestion, report aggregation, evidence calculations, and export writing. The watcher distinguishes ingesting from waiting for its next poll. These are activity indicators, not percentage-complete estimates.

Progress is displayed only when standard error is an interactive terminal. Final results stay on standard output; redirecting standard error disables progress, keeping captured output free of animation.

Spinners identify work in progress; they do not make commands faster or estimate the remaining time. To capture results and errors without animation:

```bash
metermaid report >usage-report.txt 2>usage-errors.txt
```

## Reporting limits

- **OMP overlap:** when different source generations contain matching OMP usage, selected reports warn that totals are non-authoritative. Events are retained, not automatically deduplicated. A source-session count is not a count of distinct human tasks.
- **Partial counters and cost:** a counter with no observed values appears as `unavailable`. Otherwise, totals sum only the values that were reported; a number does not prove complete coverage. Costs are source-reported, not reconstructed from a pricing table or subscription bill.
- **Supported shapes only:** a discovered source root does not prove every record format is supported. Unsupported records remain diagnostics, not zero-usage events. No quota percentages, rate-limit/stall measurements, wall-clock/API latency, task classification, or productivity scores are derived.
- **Projects:** project keys are opaque identifiers, not readable project names. There is no project-alias command.
- **Legacy history:** imported cumulative snapshots stay in a separate report section. They are not added to current event totals.

Reports combine all supplied filters for normalized events only; the separately displayed legacy-history section remains unfiltered. `--agent` accepts `claude-code`, `codex`, `pi`, or `omp`; `--model` matches the stored label and `--project-key` matches the opaque key printed in a report. Both time bounds are inclusive. Date-only values mean midnight UTC, not the end of that date; a timestamp without an offset is also interpreted as UTC.

```bash
# A bounded UTC report, including the full final date
metermaid report --since 2026-10-21T00:00:00Z --until 2026-10-27T23:59:59.999999Z
```

### Diagnostics are not all errors

`doctor` reports cumulative parse outcomes. Many `unsupported` categories are non-usage envelopes such as titles or user messages; they do not each represent a lost usage event. A `malformed` usage shape or a new unsupported shape preventing an expected metric needs investigation, not suppression. `doctor` itself does not reparse sources; use another ingest pass after new records arrive to observe changes.

Use plain `doctor` for inspection. `doctor --acknowledge` writes a review record for the displayed diagnostic state; it neither fixes records nor certifies a report and is unnecessary for ordinary use.

### Aggregate export

```bash
metermaid report
metermaid export --out metermaid_export.json
```

`export` writes all stored normalized events aggregated by agent, with a `schema_version` and `rows`. Row fields are `agent`, `event_count`, `session_count`, `tokens_in`, `tokens_out`, `cache_read`, `cache_write`, `reasoning_tokens`, and `provider_cost_usd`; absent counters serialize as JSON `null`. The CLI prints the allowlist and one sample row before writing; there is no confirmation prompt.

Export has no report filters and excludes model labels, project keys, session identifiers, timestamps, raw events, and legacy history. It also does not carry the OMP authority warning in its JSON. Review an unfiltered `report` first; any overlap warning applies to the exported totals too. Existing output files can be overwritten.

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
  metermaid.sqlite3   # events, watermarks, diagnostics, legacy history, watcher/review evidence
  metermaid.secret    # machine-local HMAC secret (0600), created on first run
```

`metermaid.secret` derives every opaque identifier the database stores; nothing here is ever a raw path, prompt, or session id. Reports and exports read only from this local database — no network calls, no shared state across machines.

SQLite may also create `metermaid.sqlite3-wal` and `metermaid.sqlite3-shm` sidecars. This is a text-free store, not an encrypted database. Protect it and its secret with normal local filesystem controls. Keep the secret with the database when backing up or restoring: changing it changes derived identities. Stop the watcher and let other Metermaid commands finish before copying the state directory.

`--data-dir` selects only the state root; it does not change which source directories are scanned. In particular, an alternate data directory alone is not a sandbox for testing against synthetic sources.

## Legacy v0.2 import

An older v0.2 install may have left per-session CSV files at `~/.metermaid/sessions/*.csv`. Metermaid v1 never reads them automatically — `ingest`/`watch` only ever produce normalized events from live sources. Importing legacy history is always explicit:

```bash
metermaid import-legacy                    # reads ~/.metermaid/sessions
metermaid import-legacy /path/to/csvs      # or an explicit directory
```

The importer reads legacy source files without modifying them and writes a separate history relation in SQLite:

- Only a CSV whose header exactly matches the original v0.2 header is imported; any other header skips the whole file.
- Only eight columns are mapped into imported history: `timestamp`, `provider`, `model`, `tokens_in`, `tokens_out`, `cache_read`, `cache_write`, `cost_usd`. Raw path, session id, deltas, sidechain fields, and context/timing columns are not mapped or persisted.
- A file with any malformed mapped numeric cell is rejected in full — no partial import.
- Imports are idempotent: re-running over the same files never duplicates rows.
- Imported rows are stored and reported as `LegacySnapshot` history, entirely separate from current observed events — `report` never merges legacy totals into the observed totals.
- The source CSV files are opened read-only and are never modified, moved, or deleted.

## Dogfood evidence

Collection validated parser correctness, historical recovery, reporting limits, and watcher/review continuity; it was not model training. The broader dogfood work produced the null-thinking-details parser correction, adapter-revision replay, an OMP overlap warning, and stored continuity/review evidence. A passing closeout allowed the separate schema advisor to proceed.

The qualification rule required at least ten qualified days within a fourteen-day window, with no incomplete days. The frozen 2026-10-12 through 2026-10-25 UTC window passed with fourteen qualified days. Earlier incomplete periods retain their original results. The rolling window shown by `status` moves with time and can fail after that frozen closeout; it does not reopen the completed campaign or require you to restart watching.

Historical campaign instructions and current operating guidance are separated in the [owner runbook](docs/dogfood-runbook.md). Local evidence ledgers stay ignored and are not part of the installation or normal usage requirements.

## Optional schema advisor

The developer-only advisor helps investigate `unsupported` record shapes. It is not a normal reporting step, a task classifier, or a stable `metermaid` subcommand. It sends only allowlisted, value-free structural summaries to a user-configured loopback Ollama endpoint and prints constrained categories. It does not change adapters, suppress diagnostics, or write Metermaid state.

The advisor is disabled unless you explicitly provide its TOML opt-in and an already available local model. It does not download or start a model. Follow the [configuration and invocation instructions](docs/superpowers/specs/2026-09-04-local-schema-advisor-design.md#configuration), then run from the checkout:

```bash
uv run python scripts/schema_advisor.py --config /path/to/schema-advisor.toml
```

Missing/disabled/invalid configuration exits nonzero before source discovery. Transport or response failures yield `needs-human-review` and a nonzero exit; a category never authorizes a production parser change. This script has its own transient tabular output, not the stable CLI's progress display.

## Uninstall and data retention

```bash
uv tool uninstall metermaid-cli
```

removes the installed CLI only. It never touches `~/.metermaid`. Deleting the state directory is a separate destructive choice that removes collected history, evidence, and the local secret, not the original agent transcripts:

```bash
rm -rf ~/.metermaid
```

(or your `--data-dir`, if you used one).

## Development

```bash
uv sync --all-groups --locked --python 3.11
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src tests && uv run pytest
```

Use an isolated `HOME`, unset `CODEX_HOME`, and an alternate state root for source-reading smokes; `--data-dir` alone still discovers your normal sources. On WSL, discovery also checks the Windows-side home under `/mnt/c/Users` independently of `HOME`, so run these smokes only in an environment without real Windows-side source roots. The clean-install test builds a wheel, installs it into a fresh environment, and exercises the seven-command workflow with synthetic fixtures; its `HOME` isolation does not disable that additional WSL discovery.

Schema evidence and fixtures follow [the fixture policy](tests/fixtures/POLICY.md). Unsupported records need a human-reviewed redacted fixture, a deterministic mapping/test, and an adapter-revision replay where required; advisor categories are not fixtures or correctness evidence.

## Documentation

| Document | Role |
| --- | --- |
| [README](README.md) | Current purpose, installation, commands, limits, and operating workflow. |
| [Owner runbook](docs/dogfood-runbook.md) | Current operation plus clearly historical collection procedures. |
| [Overview](docs/overview.md) | Current-product boundary followed by the retained, deferred expansion vision. |
| [Design](docs/design.md) | Current implementation boundary followed by deferred technical proposals. |
| [Schema advisor design](docs/superpowers/specs/2026-09-04-local-schema-advisor-design.md) | Implemented opt-in developer-tool contract and human promotion gate. |
| [Recorded-evidence design](docs/superpowers/specs/2026-09-17-self-recorded-dogfood-evidence-design.md) | Implemented evidence collection and qualification rules, not a requirement to start another campaign. |
| [Fixture policy](tests/fixtures/POLICY.md) | Redaction and human-review requirements for source-schema evidence. |

Historical plans are not a capability inventory. TUI, rate-limit/stall metrics, task classification, PATH guards, telemetry ingestion, recommendations, and a public rollout remain deferred.
