# Local schema advisor design

## Status

Implemented as the M6 developer-only aid in [`scripts/schema_advisor.py`](../../../scripts/schema_advisor.py), not as an eighth stable CLI command or an automatically enabled runtime feature. The personal pilot through M6 is complete; no public release or successor scope is approved. The design and human promotion boundary below remain the contract: no automatic parser expansion, persistence, or change to the completed M5 disposition. See [current optional-advisor usage](../../../README.md#optional-schema-advisor).

## Goal

Accelerate review of Metermaid `unsupported` diagnostics without allowing an AI model to inspect or persist transcript content, determine production parser behavior, or contact a non-local service.

The advisor groups value-free schemas for currently unsupported records and asks a user-configured local Ollama model to assign each group one advisory category. A human retains exclusive authority to promote a shape into an adapter.

## Scope

In scope:

- A developer-only source script, invoked with `uv run python`, outside Metermaid's seven stable CLI commands.
- Value-free structural summarization of locally discovered source records.
- Aggregation by agent, safe discriminator, and structural schema signature before any model call.
- An opt-in, loopback-only Ollama endpoint configured by the user.
- Machine-readable, constrained advisory categories with no free-text model rationale retained or emitted.
- A documented human promotion path from an advisory result to a redacted fixture, test, narrow adapter mapping, and adapter-revision replay.

Out of scope:

- Metermaid's normal `ingest`, `watch`, `status`, `doctor`, `report`, `export`, or `import-legacy` behavior.
- Automatic adapter changes, event writes, diagnostic suppression, database schema changes, model downloading, background service operation, or telemetry.
- Sending any data to a remote endpoint, accepting arbitrary HTTP hosts, model authentication, or persisting the model's output.
- Reopening M5. At design time, the Sep 2–15 evidence-only extension also prohibited deferred capability work; that historical restriction is not a statement that M6 remains unauthorized today.

## Configuration

The advisor reads one explicit TOML configuration file. The script accepts `--config PATH`; without it, it resolves `$XDG_CONFIG_HOME/metermaid/schema-advisor.toml`, falling back to `~/.config/metermaid/schema-advisor.toml` when `XDG_CONFIG_HOME` is unset.

The file is absent by default. The advisor refuses to run unless its dedicated opt-in is set:

```toml
[schema_advisor]
enabled = true
endpoint = "http://127.0.0.1:11434/api/generate"
model = "gemma4:12b-mlx"
timeout_seconds = 30
```

`endpoint` must be an `http` URL with the literal host `127.0.0.1`, `localhost`, or `[::1]` and the exact path `/api/generate`. An optional port must be in 1–65535; credentials, queries, fragments, and other hosts or paths are rejected before a socket is opened. `model` must be non-empty. `timeout_seconds` must be an integer in the closed interval 1–120.

The configured model selector is control-plane configuration, not a source-derived model label. Transcript model values never enter a prompt or terminal row. The transport connects directly to loopback, including IPv4 for `localhost`, without proxy-environment routing or redirects.

The normal package installation does not create this file, import advisor code, resolve a model, or open a socket.

## Architecture

```mermaid
flowchart LR
    A[Local JSONL records] --> B[Existing decoder and adapter]
    B --> C{Unsupported outcome?}
    C -- no --> X[Discard]
    C -- yes --> D[Value-free schema sanitizer]
    D --> E[Aggregate signature and count]
    E --> F[Loopback Ollama request]
    F --> G[Constrained category validator]
    G --> H[Transient terminal table]
    H --> I[Human review]
    I --> J[Redacted fixture and parser PR]
```

The script reuses documented discovery and the existing pure adapters only to identify records that already yield an `unsupported` outcome. It never writes their records, values, paths, raw identifiers, timestamps, model labels, prompt text, response text, tool arguments, tool results, or adapter outcome to the Metermaid database.

The sanitizer produces a canonical structural summary containing only:

- Pilot agent name.
- Safe top-level and nested discriminator labels already eligible for `doctor` output.
- JSON field paths.
- JSON value types (`object`, `array`, `string`, `integer`, `number`, `boolean`, `null`).
- Presence or absence of generic numeric-counter field names from a fixed source-controlled allowlist.
- Aggregate count of identical structural signatures.

It never includes a scalar value, list length, raw source path, project key, session identifier, timestamp, model name, or arbitrary field name. The existing field-name policy remains the first filter; this advisor tightens it further to a source-controlled field-path allowlist. Unknown field names become `<redacted-field>` before aggregation or request construction.

The script sends one prompt per unique structural signature, never one prompt per source record. The static prompt asks for one JSON object with a single `category` field selected from this closed set:

- `expected-envelope`
- `non-usage-message`
- `possible-usage-shape`
- `needs-human-review`

The Ollama call requests JSON output with thinking explicitly disabled (`think: false`); the approved local Gemma model otherwise exceeded the bounded inference deadline during the fixture-only smoke. The validator rejects non-JSON, extra fields, unrecognized categories, timeouts, non-2xx responses, and oversized responses. Every such failure terminates that signature with `needs-human-review`; it never invents a parser mapping or silently relabels a diagnostic.

The terminal table shows only the aggregate signature identifier, agent, safe discriminator, count, and validated category. It prints no model rationale. The script creates no database row, cache, transcript copy, log, fixture, or export.

## Developer invocation

Create the explicit TOML opt-in above, then run from the source checkout:

```bash
uv run python scripts/schema_advisor.py --config /path/to/schema-advisor.toml
```

Without `--config`, the same script uses only the documented XDG/home location. It never creates a default configuration or enables itself. Missing, disabled, or invalid configuration exits nonzero before source discovery.

The script scans complete newline-terminated records under the documented pilot-agent roots and reuses the existing pure adapters to select only `unsupported` outcomes. Supported and malformed records are not submitted. An incomplete trailing line waits for the next explicit invocation; there is no watcher, cache, checkpoint, retry loop, or advisor database. Adapter context uses an ephemeral secret, not a new or persisted Metermaid secret.

Output is a tab-separated table containing only signature ID, agent, allowlisted discriminator, aggregate count, and category. A transport/response failure emits `needs-human-review` for that group and makes the process exit nonzero. Standard output is transient; no model rationale is retained. The source entrypoint disables bytecode writes while importing its modules.

## Human promotion gate

`possible-usage-shape` is a triage priority, not proof. A human may promote a shape only by following Metermaid's existing evidence path:

1. Re-run the source-schema audit on a selected local source and review its output.
2. Create a manually reviewed redacted fixture that preserves field names and replaces every value.
3. Add an adapter contract/regression test proving the exact mapping and nullability behavior.
4. Implement one narrow deterministic adapter change.
5. Increment only that adapter's semantic revision so historical unsupported records replay safely.
6. Run the repository verification gate.

A model category never enters `ingest_diagnostics`, normalized events, reports, exports, or persisted configuration-derived data.

## Failure handling

Configuration is validated before transcript discovery. Invalid, absent, disabled, non-loopback, or malformed configuration fails loudly without opening a socket. An unavailable Ollama server or a rejected model response leaves all source data untouched and returns a non-zero process exit after printing only a structural failure category. The script has no fallback cloud provider.

## Verification

Tests must demonstrate:

- No advisor module is imported by any of the seven stable CLI commands.
- A missing config and `enabled = false` make no socket call.
- Every non-loopback endpoint and every non-Ollama path is rejected before connection.
- A seeded prompt, raw path, session ID, timestamp, model label, tool argument, and tool result cannot appear in the sanitized request, terminal output, or any created file.
- Identical sanitized schemas produce one request with the correct aggregate count.
- The response validator accepts only one closed-set category and rejects free text, extra fields, malformed JSON, and unknown categories.
- Advisor output cannot write Metermaid's SQLite store or alter `doctor` counts.
- A parser promotion still requires a redacted fixture, deterministic adapter test, and adapter revision replay.

## M5 status

M5's Aug 17–30 dogfood period is complete and its `ITERATE` disposition remains final. The Sep 2–15 evidence-only extension closed as blocked; it is not retroactively certified. M5b merged in PR #28. The separately recorded Oct 12–25 UTC formal window passed with 14 qualified days under the precommitted measured policy; later rolling coverage does not reopen that frozen closeout. That result enabled the M6 gate, and the isolated developer aid is now implemented under the unchanged local-only, value-free, no-persistence boundary. Sep 27–Oct 10 remains exploratory. See the [evidence design and closeout](2026-09-17-self-recorded-dogfood-evidence-design.md#consequence-for-the-blocked-closeout).
