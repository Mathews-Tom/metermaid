# metermaid — System Overview

**Repo:** `github.com/Mathews-Tom/metermaid`
**Status:** Design expansion of an existing tracker (formerly `codetrack`)
**Document date:** 2026-08-16
**Audience:** Engineering leadership, prospective contributors, internal pilot participants
**Companion:** [system design](system-design.md)

> **Status: deferred design reference.** This document records promotion candidates beyond the approved v1 personal pilot. It is neither a shipped-capability inventory nor an implementation authority. Rate-limit reconstruction, throttle/stall claims, task classification, TUI, PATH guard, OpenTelemetry ingest, local-model features, the privacy-safe event store/export redesign, and offline enforcement remain unapproved until their explicit evidence gates pass.

---

## 1. What

metermaid is a **local-first usage instrument for coding agents**, owned by the developer
running it, not by the organization employing them.

**Target state:** metermaid reads agent session transcripts that already exist on disk, derives per-session and per-project token attribution, reconstructs rate-limit windows, measures what being throttled actually cost, and presents all of it in a terminal. Nothing leaves the machine unless the developer explicitly exports it.

Concretely it answers four questions no existing tool answers together:

| Question | Current answer | metermaid answer |
|---|---|---|
| Where did my tokens actually go? | "In: 84k, out: 9k" | Observation / action / prose / thinking / cache-read breakdown, price-weighted |
| How often was I throttled, and what did it cost? | Nothing | Block count, mid-edit interruption rate, abandoned-thread rate, hour-of-day distribution |
| What kind of work am I spending them on? | Nothing | Deterministic task-mix distribution per session |
| Do I need a bigger plan, or better habits? | Vendor upsell | Evidence-backed recommendation across four distinct remedies |

### What it is not

- Not an admin dashboard. There is no server, no org view, no manager rollup by default.
- Not a cost tool by ambition. The current v0.2 runtime captures `costUSD` when the source reports it and otherwise uses its default pricing-table estimate. The target state narrows this to captured-only cost.
- Not a productivity scorer. The target export schema is deliberately incapable of producing a per-developer "efficiency" number; current v0.2 CSV and JSON exports still carry the fields from which one could be derived.

---

## 2. Why

### 2.1 The immediate problem

An engineering organization standardizing on Claude Code cannot account for how its
developers consume AI capacity. The visible symptoms:

- Developers hit 5-hour and weekly limits with no forewarning and no record afterward.
- Requests for higher-tier seats arrive as anecdote ("I keep running out") with no
  evidence, and are approved or denied on vibes.
- Nobody knows whether heavy consumption reflects hard problems, bad session hygiene,
  or automation quietly drawing on interactive quota.
- The organization has no way to distinguish these without instrumenting developers,
  which is culturally expensive and often legally constrained.

### 2.2 Why the obvious solution is wrong

The obvious solution is org-level telemetry: enable Claude Code's OpenTelemetry export,
ship it to a collector, build a Grafana dashboard. This is well-trodden — Anthropic
documents OTel metrics, events, and traces; SigNoz, Grafana, and AWS CloudWatch all ship
prebuilt Claude Code dashboards; AWS's Coding Agent Insights covers Claude Code, Codex,
and Copilot in one console.

It is the right answer to a different question. Org telemetry tells leadership what the
fleet consumed. It does not tell a developer what *they* should do differently, and the
act of installing it changes the relationship: the developer becomes a monitored subject.

Three concrete gaps in the org-telemetry path:

1. **No backfill.** OTel export begins observing the day it is enabled. Transcripts on
   disk go back months. Any retrospective question — "was Q2 worse than Q1" — is
   unanswerable through OTel and trivially answerable from local files.
2. **Infrastructure cost.** A collector, a time-series store, and a dashboard is a
   platform project with an owner and a budget. metermaid is `uv tool install`.
3. **Wrong subject.** Metrics grouped by department and cost center do not help the
   person deciding whether to restart their session before the context balloons.

### 2.3 The deeper reason

The thing that is actually expensive is not tokens. It is **interrupted work**.

A developer blocked mid-edit loses working state: the agent's context, their own mental
stack, the half-applied change. Two twenty-minute blocks at 11:00 and 14:00 cost far more
than one ninety-minute block at 18:00, and no existing tool distinguishes them because
none of them model the human at all.

metermaid's central bet is that **throttle cost is a real, measurable, currently
unmeasured quantity**, and that measuring it correctly is worth more than another cost
dashboard.

### 2.4 Why local-first is a design requirement, not a preference

Session transcripts contain prompts. Prompts contain proprietary code, customer names,
incident details, credentials pasted in haste, and unguarded commentary about colleagues
and employers. A tool that ships this anywhere is a data-loss incident waiting for a
trigger.

More practically: a usage tool that developers do not trust will be uninstalled, bypassed,
or fed garbage. Local-first is what makes voluntary adoption possible, and voluntary
adoption is what makes the data honest.

---

## 3. How

### 3.1 Operating principle

**Deterministic spine, isolated optional LLM surface.** Every number metermaid reports is
produced by deterministic computation over local files. A local LLM is available for a
single narrow purpose — classifying sessions the deterministic cascade abstained on — is
off by default, and its outputs are tagged with their provenance in every view and export.

### 3.2 Pipeline in one paragraph

A background watcher tails agent transcript files, parses each new record through a
per-agent connector into a normalized `UsageEvent`, and appends to a local SQLite store.
Derivation passes reconstruct 5-hour and weekly windows, detect rate-limit refusals and
the stalls they cause, decompose token spend into semantic buckets, and classify each
session's task mix. A TUI reads the store. A PATH shim runs before the agent launches, to
show remaining budget and — occasionally — ask one question. An export command emits a
schema-restricted JSON document only when the developer runs it.

### 3.3 Surfaces

| Surface | Trigger | Job |
|---|---|---|
| `metermaid watch` | Background service | Tail transcripts, populate store |
| statusline hook | Agent render loop | Capture the vendor's context-window and cost figures; quota numerator/denominator and reset data remain unverified |
| `metermaid guard` | PATH shim on `claude`/`codex`/`gemini` | Pre-flight budget line; one-key stall question |
| `metermaid` / `report` / `export` / `advise` | User-invoked | Analysis, export, recommendation |
| `metermaid mcp` | Stdio MCP client | Expose usage summaries, session lists, and cost-window reads |

### 3.4 What is already built

metermaid currently provides `watch`, `stop`, `status`, `migrate`, `hook`, `backfill`, `consolidate`, `report`, `export`, `mcp`, and `heatmap` commands for Claude Code and Codex CLI sessions. It has polling discovery, two-layer deduplication, CSV snapshots, aggregate reporting, budget/nudge output, and CSV, JSON, Markdown, HTML, and OTLP export.

| Capability | Source | State |
|---|---|---|
| Watcher, dedup, statusline parser, CSV snapshots, reports, budget, consolidation, and multi-format export | metermaid | Built |
| MCP server (`get_usage_summary`, `get_session_list`, `get_cost_windows`) | metermaid | Built |
| Main/subagent sidechain attribution | metermaid | Built for Claude; Codex sidechain values remain zero |
| Connectors for 8 agents (Claude Code, Codex, Gemini CLI, Cursor, Continue, OpenCode, Aider, Vibe) | searchat | Production, 840+ tests |
| Token composition taxonomy (observation/action/prose/prompt, price-weighted) | laconic | Runnable script, needs promotion to library |
| Window engine, stall accounting, task classifier, TUI, guard shim, and OTel ingest | — | New work |

The genuinely new engineering is the derivation layer and the guard. Ingest is a consolidation exercise, not a build.

---

## 4. Market

Verified August 2026. This space moved substantially in the preceding twelve months and
should be re-checked before any external launch.

### 4.1 Landscape

| Category | Examples | What they do | Gap |
|---|---|---|---|
| First-party org telemetry | Claude Code OTel export (metrics, events, traces); Claude Code Analytics API | Fleet-level tokens, cost, cache rates, tool accept/reject, session counts | Admin-facing; no backfill; needs a collector + TSDB + dashboard; ~1h lag on the Analytics API |
| Managed observability | SigNoz, Grafana, Datadog, AWS Coding Agent Insights | Prebuilt dashboards over the OTel feed; AWS covers Claude Code, Codex, Copilot in one console | Same OTel constraints; SaaS backend; org-scoped |
| Local cost tools | ccusage and similar | Per-session and per-window cost from transcripts | Cost only; no composition, no throttle model, no task mix |
| Live monitors | Community usage monitors, statusline scripts | Live 5h/weekly gauge in terminal | Ephemeral; no history, no derivation, no export |
| Session search | searchat and similar | Semantic retrieval over transcripts | Different problem entirely |

### 4.2 Honest read

The **org-observability** segment is crowded, first-party, and backed by AWS and the major
observability vendors. Do not enter it. Anything metermaid builds there will be worse than
what already ships and will be obsoleted by Anthropic's next release.

The **developer-facing** segment is close to empty. Every tool above answers to an admin.
None of them tell an individual engineer what to change on Monday morning. The nearest
neighbours — ccusage and the live monitors — are narrow and non-overlapping with
metermaid's core claims.

### 4.3 Positioning

> metermaid is the instrument on the developer's side of the glass.
> OTel tells your organization what you consumed. metermaid tells you what it cost you.

Complementary, not competitive: metermaid should eventually consume OTel as an additional
connector, so a shop that has already deployed the collector loses nothing by adding it.

---

## 5. Risks

Ordered by expected damage, highest first.

### R1 — Transcript schema drift (high likelihood, high impact)

The `.jsonl` transcript format is an undocumented vendor internal. Record shapes change
without notice. The single most fragile assumption in the whole design is that rate-limit
refusals are written into the transcript in a machine-parseable form with a recoverable
`reset_at`. **This must be empirically verified before anything else is built.**

*Mitigation:* golden-file corpus per agent; `metermaid doctor` reporting parsed vs.
unrecognized record counts per connector; loud failure rather than silent zeros; adapters
as pure functions so drift is a one-file fix.

### R2 — Cultural rejection (medium likelihood, project-ending impact)

Any tool that observes developers can be read as surveillance. If the first impression is
"management is now tracking my AI usage," adoption stops and does not restart.

*Mitigation:* local-only by construction; export requires an explicit command; the
developer sees the exact bytes before sending; positioned and introduced as a personal
instrument that happens to produce evidence for a seat request the developer initiates.
Never mandated. Never installed by fleet management.

### R3 — First-party absorption (medium likelihood, high impact)

Anthropic ships something similar. `/usage`-style views, richer OTel signals, or a
developer-facing local report. The composition and throttle-cost work is the part most
likely to remain unbuilt by a vendor, since the vendor has limited incentive to quantify
the cost of its own rate limits.

*Mitigation:* multi-agent scope, which no single vendor will build; keep the schema and
adapter layer as the durable asset; be ready to reposition as the aggregation layer *over*
first-party feeds.

### R4 — The stall metric is contested (high likelihood, medium impact)

Any number labeled "time lost" will be disputed by whoever does not want to approve the
spend. Overclaiming here destroys credibility for everything else in the report.

*Mitigation:* report countable quantities only — interrupt count, mid-edit fraction,
resume latency, abandoned-thread rate — and never composite them into an hours-lost figure.
Bootstrap ground truth with a one-keypress prompt rather than inferring.

### R5 — Guard shim breaks the primary tool (low likelihood, severe impact)

A PATH wrapper that fails closed makes `claude` unrunnable. One incident of this ends
fleet adoption permanently.

*Mitigation:* fail-open on every path; recursion circuit breaker; silent by default;
sub-50ms budget; a `--uninstall` that fully reverses the PATH change; ship only after the
watcher has run standalone for a pilot cycle.

### R6 — Privacy regression (low likelihood, severe impact)

A future contributor adds a field containing prompt text and it flows into an export.

*Mitigation:* no free-text field exists in the event model at all — absence rather than
redaction; export via an allowlist serializer with its own model; canary property tests in
CI asserting no seeded string reaches the store bytes or export output.

### R7 — Classifier produces confident nonsense (medium likelihood, medium impact)

A task-mix chart that always answers will be acted on. Mislabeled bins produce bad
staffing and tooling decisions.

*Mitigation:* mandatory abstention; `unclassified` as a first-class bin; per-session
distributions rather than single labels; accuracy measured against a hand-labeled set
before the chart ships.

### R8 — Scope collapse (high likelihood, medium impact)

The design touches ingest, storage, windowing, NLP, TUI, shell integration, and OSS
governance. It can absorb unlimited effort.

*Mitigation:* the phased build order, with an explicit gate per phase; the classifier and
OSS split deferred behind a working internal pilot.

---

## 6. Moat

Most of this is not defensible, and saying so up front is more useful than a slide of
imagined advantages.

### Not defensible

Parsing JSONL. Counting tokens. A terminal UI. Reconstructing 5-hour windows. Any competent
engineer reproduces these in a weekend.

### Weakly defensible

**Multi-agent breadth.** Eight working, tested connectors already exist in searchat.
Time-to-parity for a competitor is months of unglamorous format archaeology. This erodes
if someone else publishes a good adapter spec first — which is an argument for publishing
one, not for hoarding.

**Backfill.** The transcripts are already on disk. Any OTel-based competitor starts from
zero on install day. This advantage is structural and permanent.

### Genuinely defensible

**The normalization spec plus conformance corpus.** If `agent-usage-schema` becomes what
adapter authors implement against, the ecosystem accrues to it regardless of who writes
the TUI. Specs with test suites are stickier than tools. This is the asset worth
protecting.

**Composition attribution as published methodology.** The observation/action/prose/cache
decomposition is laconic's, it is defensible research rather than an implementation
detail, and it answers the one question every other tool leaves open. Publishing the
methodology strengthens rather than weakens this, because the moat is being the reference
implementation of a named measure.

**Throttle-cost accounting as a named metric.** If `abandoned_thread_rate` and
`mid_edit_interrupt_count` become the terms people use for this, metermaid owns the
definition. Vendors have negative incentive to quantify the cost of their own limits.

**Trust position.** A local-first tool with no backend, no telemetry, and CI-enforced
network isolation can make a claim that any vendor monetizing a dashboard structurally
cannot. This is a real and durable differentiator in a category that is otherwise all
surveillance-shaped.

### Realistic assessment

The moat is thin in year one and rests almost entirely on breadth-of-connectors and
being early to a developer-facing framing nobody else has taken. It thickens only if the
schema gains outside adopters. Plan accordingly: publish the spec early, treat the TUI as
disposable.

---

## 7. Examples

### 7.1 The seat request that succeeds

A developer runs `metermaid advise --window 30d`:

```
metermaid — 30d assessment

  Blocks             14  (11 × 5h, 3 × weekly)
  Mid-edit           9   (64% of blocks)
  Abandoned threads  5   (36% of interrupted sessions never resumed)
  Block hours        peaked 10:00–15:00 local
  Cache read         31% of total tokens  — within normal range
  Headless sessions  0

  → Recommendation: SEAT UPGRADE
    Blocks cluster in core working hours, most land mid-edit, and a third of
    interrupted work threads died. Session hygiene is already good (cache-read
    fraction is normal), so behavioural change has little headroom left.

  Export evidence:  metermaid export --purpose seat-request --window 30d
```

The developer chooses to send it. The manager sees a countable claim rather than
"I keep running out."

### 7.2 The seat request that shouldn't have been

Same command, different developer:

```
  Blocks             9  (9 × 5h, 0 × weekly)
  Mid-edit           2  (22% of blocks)
  Cache read         71% of total tokens  — high
  Median session     4h 20m
  Headless sessions  0

  → Recommendation: SESSION HYGIENE, then re-measure
    Nearly three quarters of consumption is re-ingested context. Sessions run
    long past the point where a restart would be cheaper. A larger plan buys
    headroom to keep paying this.
```

This is the outcome that makes the tool trustworthy to whoever holds the budget. A tool
that only ever recommends more spend gets correctly ignored.

### 7.3 The finding nobody was looking for

```
  Headless sessions  212  (41% of all sessions, 0 interactive turns)
    Top origins: pre-commit hook, nightly CI review job

  → Recommendation: SEPARATE BILLING PATH, not a seat
    Non-interactive automation is drawing on the same subscription bucket as
    interactive work. No plan tier resolves this; move it to per-token billing.
```

This class of finding is the fastest way for the project to pay for itself, and it is
invisible to every cost dashboard, because from a cost dashboard's perspective the tokens
look identical.

### 7.4 The daily interaction

Developer types `claude`. In the common case metermaid prints nothing and adds under 50ms.
Occasionally:

```
metermaid — weekly (Sonnet) at 84%, resets Thu 09:00
```

And once, the day after a block:

```
metermaid — blocked yesterday 14:32, 98m, mid-edit in auth-service
  [w]aited  [s]witched  [d]one for day  [k]ept working elsewhere  [enter] skip
```

One keypress. Stored locally. Never exported unless the developer exports it.

---

## 8. Open questions

1. Is the rate-limit refusal record machine-parseable, and does it carry `reset_at`?
   Everything in the throttle model depends on this. One day of empirical work.
2. Does the existing statusline path read Claude Code's own rendered budget figures, or
   compute them against a locally supplied denominator? The former is a vendor-supplied
   gauge; the latter inherits the unpublished-quota problem.
3. What is the actual T0 classifier accuracy against a hand-labeled set of 50 sessions?
   Determines whether tiers 2 and 3 are worth building at all.
4. Does OTel's quota/limit signal carry anything the transcript does not? If so, the OTel
   connector moves earlier in the build order.
