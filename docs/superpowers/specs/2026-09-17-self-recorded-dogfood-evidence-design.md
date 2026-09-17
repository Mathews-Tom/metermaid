# Self-recorded dogfood evidence design

## Status

Approved design. This replaces the hand-written operational record that blocked two consecutive dogfood periods. It does not reopen M5's `ITERATE` disposition, authorize M6, expand the stable command surface, or change how events are ingested, aggregated, or exported.

## Problem

The owner dogfood runbook requires a human to write down two facts every day: that the foreground watcher was running, and that the day's diagnostics were reviewed. Both periods to date failed on that record rather than on the runtime.

- M5's Aug 17–30 run was denied `STABLE` because historic manual inspection evidence was incomplete.
- The Sep 2–15 evidence extension produced tracked-agent activity on 13 of 14 calendar days, yet inspection rows exist for only 3 dates and watcher attestation exists for 2.

A protocol that has never once been satisfied is the defect. Retrying it unchanged predicts the same outcome, so the evidence a closeout depends on must be produced by the runtime as a side effect of normal use.

## Decision

Metermaid records its own operational evidence in two forms and reports coverage over it:

1. **Watcher heartbeats** make continuity measured instead of attested.
2. **Fingerprinted review acknowledgements** make diagnostic review provable instead of asserted.

Coverage is reported through the existing `status` command. The stable seven-command surface is preserved.

## Schema

Schema version 6 adds two tables through the established additive, atomic migration path. No existing table, column, index, or row is altered.

```sql
CREATE TABLE watch_heartbeats (
    observed_at TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    poll_seconds INTEGER
);

CREATE TABLE diagnostic_reviews (
    reviewed_at TEXT PRIMARY KEY,
    state_fingerprint TEXT NOT NULL,
    diagnostic_count INTEGER NOT NULL
);
```

`observed_at` and `reviewed_at` are aware-UTC timestamps at second resolution, so a poll loop cannot write unbounded duplicate rows for the same instant.

`run_id` is derived through the machine-local secret. It distinguishes an uninterrupted run from a restart without persisting a process identifier, executable path, hostname, or user name.

`state_fingerprint` is derived through the machine-local secret over the sorted `(agent, discriminator, kind, count)` rows the acknowledged `doctor` invocation displayed. Both tables hold only local runtime facts: no source record, prompt, tool argument, path, project name, or session identifier.

## Behavior

`watch` writes one heartbeat per poll as a side effect of its existing foreground loop. It gains no daemonization, process management, or signal handling.

`doctor` is unchanged by default. With `--acknowledge` it prints the identical table and then records one review row bound to that exact displayed state. Acknowledging is therefore impossible without having produced the state being acknowledged, and a later change in diagnostics yields a different fingerprint rather than inheriting an old acknowledgement.

`status` gains one coverage section over a trailing window. Per day it reports event count, heartbeat count, first and last heartbeat, longest heartbeat gap, acknowledged-review count, and a status label.

## Coverage criterion

Each day in the window is labeled exactly one of:

- **idle** — no tracked-agent events were observed. Genuine inactivity is not a failure; Sep 14 2026 is precisely this case.
- **qualified** — events were observed, at least one acknowledged review exists for that day, and no gap between consecutive heartbeats exceeds the day's largest real poll period multiplied by a fixed tolerance factor. A poll's real period is its configured interval plus the ingest work that poll performed, which on a large corpus dominates the interval outright; judging continuity against the interval alone mislabels a healthy watcher as incomplete. A heartbeat recorded before durations were measured carries no duration and falls back to its interval, which is all the evidence it holds.
- **incomplete** — events were observed but the review or continuity condition above is unmet.

The label is computed from stored evidence only. Nothing infers watcher state from a process snapshot taken after the fact, and no label is derived from the absence of a ledger row.

## Architecture

Computation stays pure and separate from persistence. The store owns only the two tables and their idempotent writes and reads. A separate module derives the fingerprint and builds the per-day coverage from events, heartbeats, and reviews, so the criterion is testable without a database or a clock.

## Alternatives rejected

**Invocation log only.** Recording that `status`, `doctor`, and `report` ran proves process execution, not human review. It would make the gate easier to pass while weakening what passing means.

**Heartbeats only.** This fixes continuity and leaves daily diagnostic review a hand-written claim, preserving half of the recurring failure.

**An eighth stable command.** Clearer naming, but it breaks the deliberately pinned seven-command contract for a readout that belongs beside the counts `status` already prints.

**Retrospective attestation for the missing dates.** Recalled attestation is exactly the weakness that blocked M5; it would certify a period from memory.

## Verification

Tests must prove:

- A changed diagnostic count yields a different fingerprint, and identical state yields a stable one.
- A day with events, an acknowledged review, and dense heartbeats is qualified.
- The same day without a review is incomplete.
- A heartbeat gap beyond tolerance makes an otherwise-reviewed day incomplete.
- A run whose polls each take far longer than the configured interval stays qualified, while a genuinely stopped watcher with equally slow polls is incomplete.
- A database holding heartbeats without durations upgrades in place, keeping those rows and their null duration.
- A day with no events is idle regardless of heartbeats or reviews.
- `doctor` records no review without the flag and exactly one with it, printing the same table either way.
- `watch` records a heartbeat per poll, and repeated writes for one second stay idempotent.
- A version 5 database upgrades in place with existing events, watermarks, and diagnostics intact.
- `--help` still lists exactly the seven stable commands.
- Ruff, format, strict mypy, and the full suite pass.

## Consequence for the blocked closeout

The Sep 2–15 extension stays blocked and no disposition is fabricated for it. Once this mechanism ships, a fresh period can be measured from recorded evidence, and its closeout becomes a readout rather than a recollection.
