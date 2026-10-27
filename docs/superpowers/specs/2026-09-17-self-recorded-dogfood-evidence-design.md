# Self-recorded dogfood evidence design

## Status

Approved design. This replaces the hand-written operational record that blocked two consecutive dogfood periods. Version 9 adds an exploratory, bounded inactive-gap derivation over the existing evidence; it does not reopen M5's `ITERATE` disposition, authorize M6, expand the stable command surface, or change how events are ingested, aggregated, or exported.

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

Schema version 6 added two tables through the established additive, atomic migration path. Version 7 added the measured `poll_seconds` column. Version 8 adds watcher-run boot evidence. Version 9 adds `continuity_policy_activations` and persists one immutable continuity-policy activation timestamp. Bounded inactive gaps remain derived at read time from the existing heartbeat and normalized-event timestamps; they add no stored gap rows or changes to historical evidence.

```sql
CREATE TABLE watch_heartbeats (
    observed_at TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    poll_seconds INTEGER
);

CREATE TABLE watch_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    booted_at TEXT
);

CREATE TABLE diagnostic_reviews (
    reviewed_at TEXT PRIMARY KEY,
    state_fingerprint TEXT NOT NULL,
    diagnostic_count INTEGER NOT NULL
);

CREATE TABLE continuity_policy_activations (
    policy_version INTEGER PRIMARY KEY CHECK (policy_version = 1),
    activated_at TEXT NOT NULL
);
```

`observed_at` and `reviewed_at` are aware-UTC timestamps at second resolution, so a poll loop cannot write unbounded duplicate rows for the same instant.

`run_id` is derived through the machine-local secret. It distinguishes an uninterrupted run from a restart without persisting a process identifier, executable path, hostname, or user name.

`state_fingerprint` is derived through the machine-local secret over the sorted `(agent, discriminator, kind, count)` rows the acknowledged `doctor` invocation displayed. These tables hold only local runtime facts: no source record, prompt, tool argument, path, project name, or session identifier.

`watch_runs.booted_at` is the host's measured UTC boot instant at watcher startup when the platform supplies one; its absence supplies no restart exemption. A continuity gap that exceeds its normal tolerance is accepted only when it crosses into a different watcher run and that later run's recorded boot instant is after the earlier heartbeat and no later than the later heartbeat. This exempts a verified system restart while preserving failure for sleep, an ordinary process restart, a restart on a platform without boot evidence, and a second restart after the same boot.

On Darwin, boot lookup invokes `/usr/sbin/sysctl` directly with a five-second timeout, independently of the terminal's `PATH`. Execution failures, timeouts, and malformed output remain explicit errors; they are not silently converted to absent boot evidence. Other platforms continue without a boot-based exemption.

## Bounded inactive gaps

Version 9 preserves every heartbeat pair but adds a transparent derived disposition for a gap that is not normal cadence or a verified reboot:

- at most one such gap may be accepted on an active UTC day;
- its duration must be no more than 30 minutes;
- no normalized event may have an `occurred_at` timestamp in `(previous heartbeat, next heartbeat]`.

An accepted interval remains visible in `status` as an inactive gap. It does not delete the interruption, invent a heartbeat, or claim that the watcher ran during it. A gap with tracked activity, a second same-day inactive gap, or a gap over 30 minutes remains unexplained and makes the day incomplete.

Heartbeat pairs are evaluated chronologically across UTC midnight and window boundaries. Each gap, including its inactive-gap budget, belongs to the UTC day of its later heartbeat. The preceding heartbeat may be outside the displayed window, and the event check includes the full gap interval rather than only the later day's events. Daily heartbeat and event counts still include only observations on that UTC day.

## Behavior

`watch` writes one heartbeat per poll as a side effect of its existing foreground loop. It gains no daemonization, process management, or signal handling.

`doctor` is unchanged by default. With `--acknowledge` it prints the identical table and then records one review row bound to that exact displayed state. Acknowledging is therefore impossible without having produced the state being acknowledged, and a later change in diagnostics yields a different fingerprint rather than inheriting an old acknowledgement.

`status` gains a live coverage section over a trailing window. Per day it reports event count, heartbeat count, first and last heartbeat, longest heartbeat gap, acknowledged-review count, accepted inactive-gap count, and a status label. Its formal verdict uses a separate trailing window whose final date is the preceding completed UTC day.

## Coverage criterion

Each day in the window is labeled exactly one of:

- **idle** — no tracked-agent events were observed. Genuine inactivity is not a failure; Sep 14 2026 is precisely this case.
- **qualified** — events were observed, at least one acknowledged review exists for that day, and every heartbeat gap is normal cadence, a verified system-restart boundary, or one accepted bounded inactive gap. A poll's real period is its configured interval plus the ingest work that poll performed, which on a large corpus dominates the interval outright; judging continuity against the interval alone mislabels a healthy watcher as incomplete. Each normal gap is judged against its own adjoining polls rather than the day's widest period, so one slow pass cannot silently excuse an unrelated interruption hours later. A heartbeat recorded before durations were measured carries no duration and falls back to its interval, which is all the evidence it holds.
- **incomplete** — events were observed but the review or continuity condition above is unmet.

The label is computed from stored evidence only. Nothing infers watcher state from a process snapshot taken after the fact, and no label is derived from the absence of a ledger row.

## Precommitted closeout rule

A formal window closes out as passing when it contains **no incomplete day** and at least **ten qualified days**. Zero incomplete days is the substantive assertion: no unexplained watcher gap, and no active day without an acknowledged review. The ten-day floor prevents a window from passing while mostly idle, while tolerating a few genuinely inactive days such as a weekend.

Schema version 9 records one immutable UTC activation timestamp for continuity policy version 1. Formal evidence starts on the following full UTC date, so the activation date itself cannot be counted from a partial day. `status` shows live trailing evidence for operational visibility, but its formal verdict uses the trailing fourteen completed UTC days only. A candidate window is ineligible until every day in it starts on or after the persisted effective date.

The 30-minute rule re-evaluates Sep 27–Oct 10 as **exploratory** evidence because the policy was chosen after Oct 4's gap was observed. Its result is useful operational evidence, not a precommitted closeout. The persisted activation row creates the boundary for the next formal window; it cannot be inserted after that window's result is known and silently reclassify it.

## Architecture

Computation stays pure and separate from persistence. The store owns the evidence tables, immutable policy activation, and their idempotent writes and reads. A separate module derives the fingerprint and builds the per-day coverage from events, heartbeats, watcher runs, and reviews, so the criterion is testable without a database or a clock.

## Alternatives rejected

**Invocation log only.** Recording that `status`, `doctor`, and `report` ran proves process execution, not human review. It would make the gate easier to pass while weakening what passing means.

**Heartbeats only.** This fixes continuity and leaves daily diagnostic review a hand-written claim, preserving half of the recurring failure.

**An eighth stable command.** Clearer naming, but it breaks the deliberately pinned seven-command contract for a readout that belongs beside the counts `status` already prints.

**Retrospective attestation for the missing dates.** Recalled attestation is exactly the weakness that blocked M5; it would certify a period from memory.

**Any watcher-run boundary.** Exempting every restart would let a user stop the foreground watcher and hide an unexplained collection gap. Only a boot instant recorded by the later watcher run can prove that the host rebooted between its adjacent heartbeats.

**Deleting a heartbeat around a gap.** Removing either endpoint would create the appearance of continuity by destroying the observation that proves the interruption. The bounded rule keeps both facts and makes an explicit, testable disposition instead.

## Verification

Tests must prove:

- A changed diagnostic count yields a different fingerprint, and identical state yields a stable one.
- A day with events, an acknowledged review, and dense heartbeats is qualified.
- The same day without a review is incomplete.
- A heartbeat gap beyond tolerance makes an otherwise-reviewed day incomplete.
- A verified host-reboot boundary between two watcher runs keeps an otherwise-reviewed day qualified.
- A process-only restart and a sleep gap remain incomplete.
- A run whose polls each take far longer than the configured interval stays qualified, while a genuinely stopped watcher with equally slow polls is incomplete.
- A version 7 database upgrades in place with its legacy heartbeats preserved and no fabricated reboot evidence.
- A 30-minute-or-less gap containing no normalized event is qualified once and displayed as an inactive gap.
- A 30-minute-plus-one-second gap, a second same-day inactive gap, and a gap containing a normalized event are incomplete.
- Cross-midnight gaps retain pre-window heartbeat and event context: a 40-minute interruption is incomplete; an event-free 30-minute gap is accepted once; an event on the preceding date still invalidates the gap; a second gap on the later date exceeds the same daily budget.
- Verified reboot evidence remains valid across midnight, without inflating the later day's heartbeat count.
- Exploratory status never reports its re-evaluated window as a precommitted closeout.
- A version 8 database upgrades once to persist one immutable continuity-policy activation timestamp.
- A formal verdict excludes the partial current UTC day and remains ineligible while its completed-day window contains any pre-activation date.
- A day with no events is idle regardless of heartbeats or reviews.
- `doctor` records no review without the flag and exactly one with it, printing the same table either way.
- `watch` records a heartbeat per poll, and repeated writes for one second stay idempotent.
- A version 5 database upgrades in place with existing events, watermarks, and diagnostics intact.
- `--help` still lists exactly the seven stable commands.
- Ruff, format, strict mypy, and the full suite pass.

## Consequence for the blocked closeout

The Sep 2–15 extension stays blocked and no disposition is fabricated for it. Sep 27–Oct 10 remains exploratory. Once policy activation is persisted, a fresh period can be measured from recorded evidence, and its closeout becomes a readout rather than a recollection.
