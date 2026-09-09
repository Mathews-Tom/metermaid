# OMP generation-overlap warning design

## Status

Approved direction; implementation gated on review of this written specification. This is a scope-limited M5 data-correctness corrective update. It does not change the completed M5 `ITERATE` disposition, authorize M6, deduplicate stored data, or expand Metermaid’s stable command surface.

## Problem

Metermaid event identities include the opaque source-session identity. That identity includes the source file generation so a truncation, rotation, or recreation cannot collide with an earlier generation. Recreating a source file containing the same OMP usage record therefore emits a second normalized event.

The September 9 audit reproduced this behavior using the synthetic OMP fixture. A value-free store audit also found normalized OMP usage signatures represented by multiple source generations.

The observed top-level OMP record identifier cannot safely become an automatic deduplication key: it is present on current message records, but a small number recur across source generations and some of those recurrences have different normalized usage fields. Silently collapsing such records would lose observed data.

## Decision

`metermaid report` marks a selection containing a possible OMP source-generation overlap as non-authoritative. It preserves every event and every existing numeric total.

The marker is selection-scoped. A date-, agent-, model-, or project-filtered report warns only when its own selected event set contains an overlap. It does not make a global claim about events that are outside the selected report.

## Detection contract

Detection runs in the pure report layer after `ReportFilter` selection. It considers only events where:

- `agent == "omp"`; and
- `provenance == "omp.message"`.

An overlap exists when two or more such selected events have different `source_session_id` values and equal values for all normalized usage semantics:

- `record_kind`, `occurred_at`, `role`, `model`, and `safe_tool_category`;
- `tokens_in`, `tokens_out`, `cache_read`, `cache_write`, and `reasoning_tokens`; and
- `provider_cost_usd`.

`event_id`, `source_session_id`, and `project_key` are deliberately excluded from the semantic signature. The first two identify source delivery rather than usage semantics. `project_key` can differ when equivalent source history is re-homed and must not conceal the warning.

This is a conservative detector, not proof of a duplicate logical usage event. Independently produced usage events can share the full signature. The output therefore uses “possible source-generation overlap” and “non-authoritative,” never “duplicate” or “deduplicated.”

## Report contract

`ObservedReport` exposes one boolean representing whether its selected totals are non-authoritative because of a possible OMP source-generation overlap.

When true, the CLI prints one warning immediately before the existing `Observed:` line. The warning contains only fixed text; it prints no identifiers, timestamps, model values, project keys, source paths, source records, or overlap counts. It states that totals retain every observed event and are not deduplicated.

When false, output is byte-for-byte unchanged. `status`, `doctor`, `export`, ingestion, persistence, migrations, event identity, and existing report totals remain unchanged.

## Alternatives rejected

### Deduplicate exact normalized repeats

This would reduce known overlap but can collapse independently identical usage events. The store does not retain enough immutable evidence to prove that every matching signature is one logical event.

### Deduplicate by OMP top-level record identifier

This would collapse observed identifier recurrences with different normalized usage. It risks data loss and invents an immutability contract that current evidence disproves.

### Query a store-level overlap table

The current report already receives normalized events and applies filters in memory. A report-layer detector is simpler, respects the selected range exactly, requires no schema migration, and persists no additional data.

## Failure handling and performance

The detector is deterministic and runs only over selected OMP usage events. It stores compact immutable tuples and one opaque source-session identifier per tuple during report construction; it retains no raw record text or raw identifiers. Empty and non-OMP selections are authoritative. A detector implementation failure is not caught or downgraded: report generation fails loudly rather than presenting totals as authoritative without a completed check.

## Verification

Tests must prove:

- Events with equal OMP usage semantics across two source sessions mark the report non-authoritative.
- Equal semantics within one source session do not mark the report.
- A changed normalized usage field across source sessions does not mark the report.
- Non-OMP events do not mark the report.
- Filtering away one member of an overlap clears the marker.
- The CLI renders the fixed non-authoritative warning only when the marker is true and never exposes sensitive values.
- Mutation removal of the marker condition makes the overlap regression fail.
- Ruff, format, strict mypy, and the complete test suite pass.

## Dogfood disposition

The local evidence-extension ledger records the confirmed data-correctness finding without claiming watcher state. After a scope-limited corrective PR passes the full gate, it receives an `ITERATE` corrective-update entry under the existing runbook. Until then, all-time OMP report totals remain non-authoritative.