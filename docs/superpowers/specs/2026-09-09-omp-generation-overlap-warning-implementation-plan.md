# OMP generation-overlap warning implementation plan

## Goal

Make `metermaid report` mark only the selected report as non-authoritative when OMP usage events with equal normalized semantics arrive from different source generations. Preserve all events and all numeric totals.

## Contract

- The report object exposes one selection-scoped boolean.
- Only `omp.message` events participate.
- A match requires equal `record_kind`, `occurred_at`, `role`, `model`, `safe_tool_category`, every token/cache/reasoning counter, and `provider_cost_usd`; it requires at least two distinct opaque `source_session_id` values.
- No row is removed, rewritten, or excluded. `status`, `doctor`, `export`, ingestion, database schema, and command names remain unchanged.
- The CLI warning is fixed privacy-safe text, appears immediately before `Observed:`, and says totals retain all events without deduplication.

## Work items

### 1. Extend the report contract

**Files:** `src/metermaid/report_v1.py`, `tests/test_report_v1.py`

Add a private semantic-key helper and a pure overlap predicate over the already selected events. Extend `ObservedReport` with the boolean and populate it in `build_report`.

The predicate keeps a dictionary from the semantic key to its first opaque source-session identifier. It returns true as soon as the same key arrives from another source session. It scans only selected OMP usage events and allocates no raw source data.

Tests:

- Positive overlap across two sessions.
- Same semantic event twice in one session stays authoritative.
- One changed usage field stays authoritative.
- Non-OMP events stay authoritative.
- A filter that removes one member clears the marker.

### 2. Render the warning

**Files:** `src/metermaid/cli.py`, `tests/test_report_v1.py`

Before the existing `Observed:` output, render a single fixed warning only when the report boolean is true. Do not alter totals, tables, empty reports, or the false-marker output.

Integration test seeds two OMP events meeting the overlap definition and asserts the warning exists. It also asserts no opaque identifier or fixture model label appears in the warning.

### 3. Prove the regression guard

**Files:** scratch worktree only, no production files

Run the positive pure report test against a mutation that disables the second-source-session condition. It must fail. Restore the code and rerun the test. This proves the test protects the detection decision rather than only exercising report rendering.

### 4. Verify and operate

Run, in order:

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
PYTHONPATH=src uv run pytest
```

Then run the local `metermaid report` against the existing store without invoking ingestion. Confirm the warning precedes the totals, the totals are unchanged by detection, and no raw source content appears.

## Dependencies

Work item 1 precedes item 2. Item 3 depends on the positive test from item 1. Item 4 depends on items 1–3. No database migration, parser change, or source replay is required.

## Risks and mitigations

- **False positive:** independently identical OMP events can have the same signature. Mitigation: call the condition a possible overlap and mark totals non-authoritative rather than deduplicating.
- **Performance:** report selection can be large. Mitigation: scan only selected OMP usage events and short-circuit on the first overlap.
- **Privacy:** tuple values exist transiently during aggregation. Mitigation: use only already-normalized fields and opaque session IDs; output fixed text only.
- **Future accidental scope growth:** warning may tempt data mutation. Mitigation: tests assert unchanged totals and keep the implementation entirely in the report layer.