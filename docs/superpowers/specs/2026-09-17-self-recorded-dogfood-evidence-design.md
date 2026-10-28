# Self-recorded dogfood evidence design

## Status

Implemented as M5b, merged in PR #28. The runtime records measured foreground-watcher continuity and explicit diagnostic-review acknowledgements; schema version 9 and continuity policy version 1 preserve the activation boundary described below. The precommitted Oct 12–25 UTC formal window passed with 14 qualified days. This is a frozen historical closeout, not a requirement to maintain a passing rolling window forever.

This specification preserves the design decisions and historical evidence windows. It does not reopen M5's `ITERATE` disposition, expand the seven-command surface, or change ingestion, reporting totals, or exports. M6 was subsequently authorized and implemented as a separate [developer-only local advisor](2026-09-04-local-schema-advisor-design.md); the earlier blocked extension is not a current M6 prohibition.

Ordinary on-demand use requires neither continuous watching nor `doctor --acknowledge`. See the [operating runbook](../../dogfood-runbook.md#on-demand-operation) and [dogfood evidence summary](../../../README.md#dogfood-evidence). An incomplete rolling day assesses this formal collection protocol, not installation health or permission to use reports.

## Problem

The original owner dogfood campaign required a human to record two facts every day: that the foreground watcher was running, and that the day's diagnostics were reviewed. The initial campaign and its extension could not establish their required coverage from that manual record:

- M5's Aug 17–30 run was denied `STABLE` because historical manual inspection evidence was incomplete; its `ITERATE` disposition remains final.
- The Sep 2–15 evidence extension lacked sufficient inspection and watcher attestation. It remains blocked rather than being retroactively certified.

Retrying that protocol unchanged would preserve the same evidence weakness. The operational record for a new formal campaign therefore became runtime-produced continuity evidence plus explicit human review acknowledgement, rather than retrospective recollection. This design does not turn the historical campaign protocol into an ordinary-use mandate.

## Decision

Metermaid records its own operational evidence in two forms and reports coverage over it:

1. **Watcher heartbeats** make continuity measured instead of attested.
2. **Fingerprinted review acknowledgements** make diagnostic review provable instead of asserted.

Coverage is reported through the existing `status` command. The stable seven-command surface is preserved.

## Schema

Schema version 6 added two tables through the established additive, atomic migration path. Version 7 added the measured `poll_seconds` column. Version 8 added watcher-run boot evidence. Version 9 added `continuity_policy_activations` and persists one immutable continuity-policy activation timestamp. Bounded inactive gaps remain derived at read time from the existing heartbeat and normalized-event timestamps; they add no stored gap rows or changes to historical evidence. Persistence is implemented in [`store.py`](../../../src/metermaid/store.py), with pure coverage derivation in [`evidence.py`](../../../src/metermaid/evidence.py).

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

`watch_runs.booted_at` is the host's measured UTC boot instant at watcher startup when the platform supplies one; its absence supplies no reboot exemption. An over-cadence gap qualifies for that exemption only when it crosses into a different watcher run and the recorded times satisfy `earlier heartbeat < later boot <= later run start <= later heartbeat`. Sleep, an ordinary process restart, a restart without boot evidence, and a second restart after the same boot do not establish a reboot boundary. Such a gap must independently meet the bounded inactive-gap rule below or remain unexplained.

On Darwin, boot lookup invokes `/usr/sbin/sysctl` directly with a five-second timeout, independently of the terminal's `PATH`. Execution failures, timeouts, and malformed output remain explicit errors; they are not silently converted to absent boot evidence. Other platforms continue without a boot-based exemption.

## Bounded inactive gaps

Version 9 preserves every heartbeat pair but adds a transparent derived disposition for a gap that is not normal cadence or a verified reboot:

- at most one such gap may be accepted on an active UTC day;
- its duration must be no more than 30 minutes;
- no normalized event may have an `occurred_at` timestamp in `(previous heartbeat, next heartbeat]`.

An accepted interval remains visible in `status` as an inactive gap. It does not delete the interruption, invent a heartbeat, or claim that the watcher ran during it. A gap with tracked activity, a second same-day inactive gap, or a gap over 30 minutes remains unexplained and makes the day incomplete.

Heartbeat pairs are evaluated chronologically across UTC midnight and window boundaries. Each gap, including its inactive-gap budget, belongs to the UTC day of its later heartbeat. The preceding heartbeat may be outside the displayed window, and the event check includes the full gap interval rather than only the later day's events. Daily heartbeat and event counts still include only observations on that UTC day.

## Behavior

`watch` writes one heartbeat per completed ingest poll as a side effect of its existing foreground loop. It gains no daemonization, process management, or separate stop command; Ctrl+C stops that foreground loop.

`doctor` is unchanged by default. With `--acknowledge` it prints the same discovery/outcome tables, then records one review row bound to that exact displayed state and prints an acknowledgement confirmation. Acknowledging is therefore impossible without having produced the state being acknowledged, and a later change in diagnostics yields a different fingerprint rather than inheriting an old acknowledgement. The flag neither repairs nor suppresses diagnostics.

`status` shows live coverage over a trailing window: per-day event and heartbeat counts, longest heartbeat gap, acknowledged-review count, accepted inactive-gap count, and status label. Its formal verdict uses a separate trailing fourteen-day window whose final date is the preceding completed UTC day. These readouts are implemented in [`cli.py`](../../../src/metermaid/cli.py); first and last heartbeat timestamps are not displayed as table columns.

## Coverage criterion

Each day in the window is labeled exactly one of:

- **idle** — no tracked-agent events were observed. Genuine inactivity is not a failure; Sep 14 2026 is precisely this case.
- **qualified** — events were observed, at least one acknowledged review exists for that day, and every heartbeat gap is normal cadence, a verified system-restart boundary, or one accepted bounded inactive gap. A poll's real period is its configured interval plus measured ingest duration; legacy heartbeats without duration fall back to the interval. Normal cadence permits a gap no larger than twice the slower of its two adjoining poll periods, not the day's widest period. Thus one slow pass cannot silently excuse an unrelated interruption hours later.
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
- A process-only restart or sleep supplies no reboot exemption; an over-cadence gap must independently meet the bounded inactive-gap rule or remain incomplete.
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
- `doctor` records no review without the flag and exactly one with it, displaying the same discovery/outcome tables and adding only the acknowledgement confirmation.
- `watch` records a heartbeat per poll, and repeated writes for one second stay idempotent.
- A version 5 database upgrades in place with existing events, watermarks, and diagnostics intact.
- `--help` still lists exactly the seven stable commands.
- Ruff, format, strict mypy, and the full suite pass.

## Consequence for the blocked closeout

The Sep 2–15 extension stays blocked and no disposition is fabricated for it. Sep 27–Oct 10 remains exploratory because the bounded inactive-gap rule was chosen after a gap in that window was observed. Neither historical window was retroactively certified.

With policy activation recorded, the separate Oct 12–25 UTC formal window passed with 14 qualified days under the precommitted rule: no incomplete day and at least ten qualified days. This completed M5b's measured closeout and enabled the subsequent M6 developer-advisor gate. The passing window is frozen; `status` continues to calculate a moving live window and the latest fourteen completed UTC days, so a later rolling failure neither alters the stored historical evidence nor reopens that result.
