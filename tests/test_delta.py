"""Tests for delta tracking via state sidecar files."""

from __future__ import annotations

from pathlib import Path

from metermaid.delta import compute_deltas, load_state, save_state
from metermaid.models import Snapshot


def _snap(tokens_in: int = 1000, tokens_out: int = 500) -> Snapshot:
    return Snapshot(
        timestamp="2025-01-01T00:00:00", provider="claude", model="test",
        session_id="abc", cost_usd=0.0, ctx_pct=0.0, ctx_tokens=0,
        ctx_max=0, wall_sec=0.0, api_sec=0.0, tokens_in=tokens_in,
        tokens_out=tokens_out, cache_read=0, cache_write=0,
        diff_add=0, diff_del=0, path="", source="watcher",
        tok_in_delta=0, tok_out_delta=0,
        sc_tokens_in=0, sc_tokens_out=0, sc_cost_usd=0.0, sc_models="",
    )


def test_first_snapshot_deltas_equal_cumulative(tmp_path: Path) -> None:
    snap = compute_deltas(_snap(1000, 500), state_dir=tmp_path)
    assert snap.tok_in_delta == 1000
    assert snap.tok_out_delta == 500


def test_subsequent_snapshot_computes_delta(tmp_path: Path) -> None:
    compute_deltas(_snap(1000, 500), state_dir=tmp_path)
    snap2 = compute_deltas(_snap(1800, 700), state_dir=tmp_path)
    assert snap2.tok_in_delta == 800
    assert snap2.tok_out_delta == 200


def test_corrupt_state_file_resets(tmp_path: Path) -> None:
    (tmp_path / "claude_abc.state").write_text("garbage")
    assert load_state("claude", "abc", tmp_path) == (0, 0)


def test_state_file_persistence(tmp_path: Path) -> None:
    save_state("claude", "abc", 5000, 2000, tmp_path)
    assert load_state("claude", "abc", tmp_path) == (5000, 2000)


def test_state_dir_created(tmp_path: Path) -> None:
    new_dir = tmp_path / "subdir" / "state"
    save_state("codex", "xyz", 100, 50, new_dir)
    assert (new_dir / "codex_xyz.state").exists()
