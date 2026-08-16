"""Delta tracking via state sidecar files.

Stores (tok_in, tok_out) per session in ~/.metermaid/state/{provider}_{session_id}.state.
Each snapshot gets tok_in_delta and tok_out_delta computed from the previous state.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .models import STATE_DIR, Snapshot


def load_state(
    provider: str, session_id: str, state_dir: Path = STATE_DIR
) -> tuple[int, int]:
    """Load previous (tok_in, tok_out) from sidecar .state file."""
    state_file = state_dir / f"{provider}_{session_id}.state"
    if state_file.exists():
        try:
            parts = state_file.read_text().strip().split(",")
            return int(parts[0]), int(parts[1])
        except (ValueError, IndexError, OSError):
            pass
    return 0, 0


def save_state(
    provider: str,
    session_id: str,
    tok_in: int,
    tok_out: int,
    state_dir: Path = STATE_DIR,
) -> None:
    """Persist current cumulative tokens for next delta computation."""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / f"{provider}_{session_id}.state"
    state_file.write_text(f"{tok_in},{tok_out}")


def compute_deltas(snap: Snapshot, state_dir: Path = STATE_DIR) -> Snapshot:
    """Load prev state, compute deltas, save new state, return updated Snapshot."""
    prev_in, prev_out = load_state(snap.provider, snap.session_id, state_dir)
    delta_in = max(0, snap.tokens_in - prev_in)
    delta_out = max(0, snap.tokens_out - prev_out)
    save_state(
        snap.provider, snap.session_id, snap.tokens_in, snap.tokens_out, state_dir
    )
    return replace(snap, tok_in_delta=delta_in, tok_out_delta=delta_out)
