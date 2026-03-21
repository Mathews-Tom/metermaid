# CLAUDE.md

## Project: metermaid

Background usage tracker for Claude Code and Codex CLI sessions.

## Constraints

- Single dependency: `rich` for terminal UI. No other external packages.
- Type hints on every function signature.
- Each module under 150 lines for business logic. Display-heavy modules (cli, watcher, report) may reach 170.
- Relative imports within the package.

## Architecture

- `models.py` owns the `Snapshot` dataclass — the single shared data model. Everyone imports from there.
- `parsers/` is the only place that touches JSONL structure.
- `cli.py` is thin — dispatches to other modules, no business logic.
- Cost: capture `costUSD` when present, leave analysis to ccusage. Don't estimate cost from tokens.
- **Per-session CSV files**: One file per session at `~/.metermaid/sessions/{provider}_{session_id}.csv`. No shared read-modify-write. Safe across multiple terminals/tabs/watchers. Reports scan all session files and merge on read.

## Testing

- pytest for tests, run with: `pytest tests/`
- Use `tmp_path` fixtures for file I/O tests.
- Realistic JSONL fixtures in tests.

## Commands

```bash
# Run tests
uv run pytest tests/

# Install editable
uv tool install -e .

# Type check
uv run mypy src/metermaid/
```
