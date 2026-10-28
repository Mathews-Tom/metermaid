# Operating and historical dogfood runbook

Metermaid is a completed personal pilot, not a hosted service or a billing system. Ordinary use is on demand; it does **not** require a collection campaign, an always-running watcher, a daily ledger, or diagnostic acknowledgements. See [purpose and status](../README.md#purpose-and-status) and [everyday use](../README.md#everyday-use).

## On-demand operation

After using a supported agent, ingest available records and then inspect the stored report:

```bash
metermaid ingest
metermaid report
```

`ingest` discovers the documented Claude Code, Codex CLI, Pi, and OMP sources and performs one bounded incremental pass. A large backlog can require repeated `ingest` calls or several watcher polls; a completed pass is not a promise that every historical byte has been consumed. Incomplete trailing JSONL records wait for a later pass.

`report` reads events already in the store; it does not ingest. For example, select current OMP events from an inclusive UTC lower bound:

```bash
metermaid report --agent omp --since 2026-01-01T00:00:00Z
```

Other current-event selectors are `--until`, `--model`, and `--project-key`. Both timestamp bounds are inclusive; timestamps without an offset are interpreted as UTC. Imported v0.2 legacy history is printed separately and is not included in these current-event filters or totals.

All seven commands accept `--data-dir` to select a local state directory. Use the same directory for ingestion and later reads, for example:

```bash
metermaid ingest --data-dir /path/to/state
metermaid report --data-dir /path/to/state
```

This changes the store and local secret location, **not** transcript discovery roots. Default state is under `~/.metermaid`. There is no arbitrary transcript-directory CLI flag; discovery uses the documented agent layouts, including `CODEX_HOME` for an additional Codex root.

## Optional foreground watcher

For repeated ingestion while working, use a dedicated terminal:

```bash
metermaid watch --interval 30
```

The interval must be a positive integer number of seconds. Each poll finishes its ingest pass before waiting that interval, so the real period includes ingest time. Stop it with **Ctrl+C in that terminal**; the loop prints `Stopped`. There is no `stop` command, managed daemon, service-manager integration, or need to keep watching between sessions. Restart `watch` or run `ingest` whenever another pass is wanted. Stopping the watcher does not prevent reporting from the existing store.

## Progress feedback and output

On terminal stderr, Rich shows transient, stage-labeled activity spinners while opening the store, ingesting, checking diagnostics, aggregating, exporting, or waiting for the next watcher poll. They indicate activity, not percent complete or a speedup. Command results, tables, summaries, and report warnings remain on stdout. Redirecting stderr disables progress; redirecting stdout alone does not.

```bash
metermaid report > report.txt 2> report-errors.txt
```

The redirected stderr file receives no progress animation; actual errors are not suppressed. See [progress feedback](../README.md#progress-feedback).

## Diagnostics, warnings, and errors

Use these readouts when investigating missing or surprising observations; there is no required daily invocation order:

```bash
metermaid status
metermaid doctor
metermaid report
```

- `status` shows stored aggregate counts, source-root discovery, and rolling dogfood evidence. An `incomplete` day or a rolling `criteria do not pass` message is a collection-coverage assessment, not a command failure or an ordinary-use requirement.
- `doctor` shows source-root presence separately from enabled adapters, plus stored `parsed`, `malformed`, and `unsupported` outcomes. A missing alternative root is not by itself a failed installation. `unsupported` means a shape was not mapped; `malformed` signals an invalid record for the parser. These are diagnostic data, not proof that the command failed. Inspect changes relevant to a report's coverage; do not hide diagnostics to make a report appear clean.
- `report` may warn that selected OMP totals are **non-authoritative** because of possible source-generation overlap. The warning is selection-scoped and does not deduplicate anything: every observed event and numeric total is retained. The same limitations apply to aggregate exports: `export` includes all current normalized events, without report filters or legacy rows, but its JSON has no overlap warning or authority flag. Review an unfiltered `metermaid report` before relying on an export.
- Missing counters are `unavailable`, not inferred zero. A sum can be partial when only some events report a field. Costs are source-reported, not price-derived or an authoritative bill. Reports do not establish productivity, savings, or complete provider usage.
- Invalid arguments/configuration, store failures, and other runtime exceptions are errors, distinct from the informational tables and warnings above. A failed command is not evidence of a completed pass. Individual source read/stat errors can instead cause that file to be skipped for the pass, so a successful summary is not proof of complete source coverage.

`doctor --acknowledge` records that its displayed diagnostic state was reviewed. It does not fix, clear, or accept a diagnostic and is **not** needed for ordinary operation. It exists for explicit measured evidence campaigns. For report limits, see [reporting limits](../README.md#reporting-limits).

## Completed evidence and historical campaign procedure

The original M5 Aug 17–30 campaign ended with `ITERATE`; the Sep 2–15 extension remains blocked, and Sep 27–Oct 10 remains exploratory. The separate, precommitted **Oct 12–25 UTC** formal window passed with 14 qualified days. Later rolling `status` failures do not reopen that frozen result or require a new campaign. No public release or successor scope follows automatically from this closeout.

M5b replaced manual continuity attestation with recorded watcher heartbeats and fingerprinted reviews. Its unchanged measured criteria, including the bounded inactive-gap policy, are documented in the [self-recorded evidence specification](superpowers/specs/2026-09-17-self-recorded-dogfood-evidence-design.md). These are formal-campaign criteria, not daily-use gates.

The remainder preserves the **original M5 campaign instructions** for historical context. Its continuous-watch, mandatory-triage, manual-ledger, and disposition instructions applied to that campaign only; they are not instructions to restart it or a substitute for M5b measured evidence.

### Historical scope

- One participant: the project owner. No recruiting, no centralized
  telemetry, no automatic-update mechanism, and no wrapper, hook, or
  prompt injected into any tracked agent.
- Duration: 14 consecutive calendar days of active, continuously watched use. The clock and its progress were tracked only in the local ledger, never in this file.
- A corrective update did not restart the 14-day clock: dogfooding continued on the corrected build for the remainder of the window.
- The watcher stayed read-only with respect to transcripts: it never blocked an agent, prompted for input, installed a wrapper, or uploaded data anywhere.

### Historical continuous foreground watch

For the whole original dogfood period, the procedure required a dedicated foreground `metermaid watch` terminal during every tracked-agent session. The owner started and stopped it by hand with Ctrl+C and restarted it before the next session. This continuous-watch rule is archived, not an ordinary-use mandate.

### Historical inspection and diagnostic triage

The original campaign required `status`, `doctor`, and `report`, in that order, after observed source changes or at least once per agent session. Every `malformed` or `unsupported` entry had to be triaged before relying on the accompanying report:

1. **Reproduce it.** Re-run `doctor` after the same source produces new records; a diagnostic recurring for the same agent/discriminator pair is reproducible.
2. **Classify it.** Parser, correctness, privacy, or data-loss defect — or a genuinely unsupported shape, which stays visible in `doctor`.
3. **Log the triage outcome** — classification and reproducibility
   only, never the record itself — in the local ledger.

### Historical defect triage and corrective-update loop

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

### Historical local ledger

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

#### Ledger template

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

### Historical final disposition

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

These archived instructions do not certify a historical outcome. The completed measured closeout is described above; the original local ledger and its historical dispositions are not rewritten by this runbook.
