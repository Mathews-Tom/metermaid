# AGENTS.md

## Code Review Rules

- **What failure means here:** prioritize these concrete regressions in Metermaid's private, local usage ledger:
  - Privacy: `src/metermaid/ingest.py`, `state.py`, `store.py`, or `export_v1.py` persist transcript content/raw paths, weaken opaque identifiers, or expose fields outside the restricted aggregate export allowlist.
  - Accounting: `src/metermaid/ingest.py` or `store.py` lose/double-count complete records on resume, rotation, or adapter replay, advance past incomplete lines, or commit events separately from diagnostics/watermarks.
  - Reporting: `src/metermaid/report_v1.py` or `legacy_v1.py` turn unobserved counters into zero, apply filters incorrectly, merge legacy snapshots into current totals, or silently present overlapping OMP usage as authoritative.
  - CLI: `src/metermaid/cli.py` breaks documented commands/flags or exit behavior, adds service calls to normal commands, or leaks interactive progress into redirected output.
- **Do not report:** style, formatting, naming preferences, or checks already enforced by `ruff check`, `ruff format --check`, and strict `mypy`; speculative hardening without a reachable failing scenario; findings already raised on an earlier commit of this PR.
- Treat defects outside the PR's changes as **Pre-existing**, not requested fixes; mention them only when necessary to explain a changed-code failure.
- **Paths to skip:** `uv.lock` and synthetic payloads in `tests/fixtures/m3/*.jsonl` and `tests/fixtures/m4/*.csv`; inspect them only when the PR changes dependency behavior or fixture semantics relevant to a demonstrated failure. Do not skip the tests that consume them.
- **Evidence and format:** every finding names `file:line`, its bug class, and a concrete triggering input/state plus the incorrect result; trace behavior rather than guessing from names. Describe the class so the author can fix every affected instance and consolidate duplicates.
- Tag every finding **Important**, **Nit**, or **Pre-existing**. Begin the summary with the count of **Important** findings; when none exist, say plainly “0 Important findings; nothing to fix.” Do not manufacture nits to fill the review.
- **Recurring rule:** compare changed documentation claims with actual schema persistence, ingestion limits, and platform discovery; flag concrete behavioral contradictions, not preferred wording.
