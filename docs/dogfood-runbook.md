# Owner dogfood runbook

Operating procedure for M5 — "Active personal dogfood and issue-driven
updates" (`.docs/DEVELOPMENT_PLAN.md`), as resolved by decision DM-004.
The project owner is the sole dogfood participant. This document is the
day-to-day procedure only. It does not state a start date, an elapsed
duration, or an outcome — those live only in the local ledger described
in [Local ledger](#local-ledger), and only once the period is over.

## Scope

- One participant: the project owner. No recruiting, no centralized
  telemetry, no automatic-update mechanism, and no wrapper, hook, or
  prompt injected into any tracked agent.
- Duration: 14 consecutive calendar days of active, continuously
  watched use. The clock and its progress are tracked only in the local
  ledger, never in this file.
- A corrective update (see below) does not restart the 14-day clock:
  dogfooding continues on the corrected build for the remainder of the
  window.
- The watcher stays read-only with respect to transcripts: it never
  blocks an agent, prompts for input, installs a wrapper, or uploads
  data anywhere.

## Continuous foreground watch

For the whole dogfood period, run the watcher in the foreground, in a
terminal dedicated to it, for every session in which the owner actively
uses a tracked agent (Claude Code, Codex CLI, Pi, or OMP):

```bash
metermaid watch
```

`watch` runs in the foreground until interrupted with `Ctrl+C`. It is
never run backgrounded, daemonized, or under a service manager — the
owner starts and stops it by hand, and it must be running again before
the next agent session starts.

## After each observed source change

Whenever the foreground `watch` output shows it ingested new records —
or once per agent session, whichever happens more often — run the three
inspection commands in this exact order:

```bash
metermaid status
metermaid doctor
metermaid report
```

- `status` — aggregate event/session/diagnostic counts and the
  source-discovery table.
- `doctor` — per-agent, per-discriminator parse outcomes (`parsed` /
  `malformed` / `unsupported`).
- `report` — observed-event totals and breakdowns; must be reviewed
  before it is relied on (see triage below).

## Diagnostic triage (mandatory, every time)

Every `malformed` or `unsupported` entry `doctor` reports must be
triaged before the accompanying `report` is trusted:

1. **Reproduce it.** Re-run `doctor` after the same source produces new
   records; a diagnostic that recurs for the same agent/discriminator
   pair is reproducible.
2. **Classify it.** Parser, correctness, privacy, or data-loss defect —
   or a genuinely unsupported record shape, which stays visible in
   `doctor` and is never hidden to make a report look clean.
3. **Log the triage outcome** — classification and reproducibility
   only, never the record itself — in the local ledger.

## Defect triage and corrective-update loop

A reproducible parser, correctness, privacy, or data-loss defect ships
only through this bounded sequence — never a direct edit to a running
install:

1. **Redacted reproduction first.** Build a deterministic reproduction
   with `tests.fixture_helpers.redacted_record` (field names only,
   every value replaced with `<redacted>`), or, for a real local
   source, run `uv run python scripts/audit_source_schema.py
   <source-jsonl>` per `tests/fixtures/POLICY.md`. Never commit a raw
   record, prompt, tool argument, path, or session id.
2. **Regression test.** Add a test under `tests/` that fails against
   the pre-fix code with the redacted reproduction and passes once the
   fix lands.
3. **One scope-limited fix PR.** The fix addresses only the triaged
   defect; it never expands into a deferred capability listed in
   `.docs/DEVELOPMENT_PLAN.md` section 7.
4. **Full gate before merge:**

   ```bash
   uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest
   ```

5. **Log the update** — PR reference and one-line classification only —
   in the local ledger as an `ITERATE` entry, then resume continuous
   watching on the corrected build.

## Local ledger

A per-run ledger records dates, triage outcomes, corrective updates,
and the final disposition. It lives at `.docs/m5-dogfood-ledger.md`.
`.gitignore` excludes the whole `.docs/` directory; the ledger must
never be `git add`ed, committed, or pushed. Recreate it from
[the template below](#ledger-template) at the start of a run.

The ledger must never contain: a raw transcript line, a prompt, a tool
argument, a raw file or project path, a raw session id, a raw event
payload, or any identifying aggregate (a per-project or per-session
count small enough to re-identify a workspace or session). It records
only dates, command names, diagnostic classifications, PR references,
and the final disposition.

### Ledger template

```markdown
# M5 dogfood ledger (local, ignored — never commit)

Period: <start-date> through <start-date + 13 days>

## Daily log

| Date | watch running | status/doctor/report run | diagnostics seen | triage outcome |
| --- | --- | --- | --- | --- |

## Corrective updates

| Date opened | Classification | Regression test added | PR reference | Date closed |
| --- | --- | --- | --- | --- |

## Final disposition

Recorded only after all 14 calendar days have elapsed.

Disposition: <STABLE | ITERATE | STOP>
Rationale (one paragraph, no raw data):
```

## Final disposition

Record exactly one disposition in the local ledger, and only after the
full 14 calendar days have elapsed — never on the day the period
starts, and never mid-period:

- **STABLE** — the owner completed 14 calendar days; no privacy or
  data-loss incident occurred; every observed diagnostic was triaged;
  `report` provided at least one previously unknown, actionable
  observation.
- **ITERATE** — a reproducible defect was found and corrected through
  the loop above; dogfooding continues on the corrected build without
  adding a deferred feature.
- **STOP** — the report produced no useful observation, or the
  privacy/trust boundary failed.

This runbook is the procedure. It asserts nothing about whether a
14-day period has started, is under way, or has produced a result.
