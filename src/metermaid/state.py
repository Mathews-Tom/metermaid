"""State paths and opaque identifiers for the local Metermaid v1 store."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path

DATABASE_FILENAME = "metermaid.sqlite3"
SECRET_FILENAME = "metermaid.secret"
_SECRET_BYTES = 32


@dataclass(frozen=True, slots=True)
class StatePaths:
    """The v1-owned files under one explicit state root."""

    root: Path
    database: Path
    secret: Path


def resolve_state_paths(data_dir: Path | None = None) -> StatePaths:
    """Resolve v1 state without creating or touching user files."""
    root = data_dir if data_dir is not None else Path.home() / ".metermaid"
    return StatePaths(
        root=root,
        database=root / DATABASE_FILENAME,
        secret=root / SECRET_FILENAME,
    )


def load_or_create_secret(paths: StatePaths) -> bytes:
    """Return the machine-local HMAC secret, creating only the v1 secret file."""
    paths.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if paths.secret.exists():
        secret = paths.secret.read_bytes()
        if len(secret) != _SECRET_BYTES:
            raise ValueError(f"Invalid Metermaid secret length at {paths.secret}")
        return secret

    secret = os.urandom(_SECRET_BYTES)
    descriptor = os.open(paths.secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as secret_file:
        secret_file.write(secret)
    return secret


def opaque_identifier(secret: bytes, namespace: str, value: str) -> str:
    """Derive a stable, namespaced opaque identifier without retaining ``value``."""
    if not namespace or not value:
        raise ValueError("Opaque identifier inputs must be non-empty")
    message = namespace.encode("utf-8") + b"\x00" + value.encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def event_identifier(secret: bytes, *parts: str) -> str:
    """Derive a deterministic event identifier with unambiguous component framing."""
    if not parts or any(not part for part in parts):
        raise ValueError("Event identifiers require non-empty components")
    message = b"".join(
        len(part.encode("utf-8")).to_bytes(4, "big") + part.encode("utf-8")
        for part in parts
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()
