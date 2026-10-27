"""Opt-in, value-free schema grouping for the development-only advisor."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .discover import PILOT_AGENTS
from .schema_audit import MAX_SAFE_FIELD_NAME_LENGTH


class AdvisorError(Exception):
    """A failure whose message contains no configuration or source content."""


@dataclass(frozen=True, slots=True)
class AdvisorConfig:
    endpoint: str = field(repr=False)
    model: str = field(repr=False)
    timeout_seconds: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class SchemaGroup:
    signature_id: str
    agent: str
    discriminator: str
    schema_json: str
    count: int


_DISCRIMINATORS = frozenset(
    {"assistant", "event_msg", "token_count", "message", "unknown"}
)
_ENDPOINT = re.compile(
    r"http://(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?/api/generate",
    re.IGNORECASE | re.ASCII,
)
_CONFIG_FIELDS = frozenset({"enabled", "endpoint", "model", "timeout_seconds"})
_REDACTED_FIELD = "<redacted-field>"
_MAX_DEPTH = 32
_MAX_NODES = 100_000

# Only reviewed paths retain names. An unknown parent or an array boundary
# cannot grant its children the privileges of a reviewed object path.
_ROOT_FIELDS = frozenset({"type", "timestamp", "message"})
_MESSAGE_FIELDS = frozenset({"role", "model", "timestamp", "usage"})
_CLAUDE_USAGE_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "output_tokens_details",
    }
)
_MESSAGE_USAGE_FIELDS = frozenset(
    {"input", "output", "cacheRead", "cacheWrite", "reasoningTokens", "cost"}
)
_FIELD_PATHS: dict[str, dict[tuple[str, ...], frozenset[str]]] = {
    "claude-code": {
        (): _ROOT_FIELDS,
        ("message",): _MESSAGE_FIELDS,
        ("message", "usage"): _CLAUDE_USAGE_FIELDS,
        ("message", "usage", "output_tokens_details"): frozenset({"thinking_tokens"}),
    },
    "codex": {
        (): frozenset({"type", "timestamp", "payload"}),
        ("payload",): frozenset({"type", "info"}),
        ("payload", "info"): frozenset({"total_token_usage"}),
        ("payload", "info", "total_token_usage"): frozenset(
            {
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "reasoning_output_tokens",
            }
        ),
    },
    "pi": {
        (): _ROOT_FIELDS,
        ("message",): _MESSAGE_FIELDS,
        ("message", "usage"): _MESSAGE_USAGE_FIELDS,
        ("message", "usage", "cost"): frozenset({"total"}),
    },
    "omp": {
        (): _ROOT_FIELDS,
        ("message",): _MESSAGE_FIELDS | frozenset({"toolName"}),
        ("message", "usage"): _MESSAGE_USAGE_FIELDS,
        ("message", "usage", "cost"): frozenset(
            {"input", "output", "cacheRead", "cacheWrite", "total"}
        ),
    },
}

Schema = str | dict[str, object]


def validate_config(config: AdvisorConfig) -> None:
    """Validate both loaded and directly constructed configs before network use."""
    if (
        not isinstance(config.endpoint, str)
        or _ENDPOINT.fullmatch(config.endpoint) is None
        or not isinstance(config.model, str)
        or not config.model.strip()
        or type(config.timeout_seconds) is not int
        or not 1 <= config.timeout_seconds <= 120
    ):
        raise AdvisorError("needs-human-review")
    try:
        endpoint = urlsplit(config.endpoint)
        port = endpoint.port
    except ValueError:
        raise AdvisorError("needs-human-review") from None
    if port is not None and not 1 <= port <= 65535:
        raise AdvisorError("needs-human-review")


def load_config(path: Path | None = None) -> AdvisorConfig:
    """Load the dedicated opt-in without creating files or revealing its values."""
    try:
        if path is None:
            xdg_home = os.environ.get("XDG_CONFIG_HOME")
            root = Path(xdg_home) if xdg_home is not None else Path.home() / ".config"
            path = root / "metermaid" / "schema-advisor.toml"
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, ValueError, RuntimeError):
        raise AdvisorError("needs-human-review") from None

    section = document.get("schema_advisor")
    if (
        set(document) != {"schema_advisor"}
        or not isinstance(section, dict)
        or set(section) != _CONFIG_FIELDS
        or section.get("enabled") is not True
    ):
        raise AdvisorError("needs-human-review")
    endpoint: object = section["endpoint"]
    model: object = section["model"]
    timeout: object = section["timeout_seconds"]
    if (
        not isinstance(endpoint, str)
        or not isinstance(model, str)
        or type(timeout) is not int
    ):
        raise AdvisorError("needs-human-review")
    config = AdvisorConfig(endpoint, model, timeout)
    validate_config(config)
    return config


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _schema_for(
    value: object,
    paths: Mapping[tuple[str, ...], frozenset[str]],
    path: tuple[str, ...] | None,
    depth: int,
    remaining: list[int],
) -> Schema:
    remaining[0] -= 1
    if depth > _MAX_DEPTH or remaining[0] < 0:
        raise AdvisorError("needs-human-review")
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AdvisorError("needs-human-review")
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        items: dict[str, Schema] = {}
        # No reviewed field paths cross an array: item positions and counts
        # carry no meaning in this summary.
        for item in value:
            schema = _schema_for(item, paths, None, depth + 1, remaining)
            items[_canonical(schema)] = schema
        return {"type": "array", "items": [items[key] for key in sorted(items)]}
    if isinstance(value, Mapping):
        allowed = paths.get(path, frozenset()) if path is not None else frozenset()
        fields: dict[str, Schema] = {}
        unknown: dict[str, Schema] = {}
        for name, item in value.items():
            if not isinstance(name, str):
                raise AdvisorError("needs-human-review")
            known = (
                name in allowed
                and name.isascii()
                and name.isidentifier()
                and len(name) <= MAX_SAFE_FIELD_NAME_LENGTH
            )
            child_path = (*path, name) if known and path is not None else None
            schema = _schema_for(item, paths, child_path, depth + 1, remaining)
            if known:
                fields[name] = schema
            else:
                unknown[_canonical(schema)] = schema
        if unknown:
            # A set union avoids both occurrence indices and last-key-wins
            # loss of structural types when multiple unknown keys collide.
            fields[_REDACTED_FIELD] = {
                "anyOf": [unknown[key] for key in sorted(unknown)]
            }
        return {"type": "object", "fields": fields}
    raise AdvisorError("needs-human-review")


def group_records(
    records: Iterable[tuple[str, str, Mapping[str, object]]],
) -> tuple[SchemaGroup, ...]:
    """Aggregate canonical, value-free signatures; never retain decoded records."""
    groups: dict[str, SchemaGroup] = {}
    for agent, discriminator, record in records:
        if agent not in PILOT_AGENTS or not isinstance(record, Mapping):
            raise AdvisorError("needs-human-review")
        safe_discriminator = (
            discriminator if discriminator in _DISCRIMINATORS else "unknown"
        )
        schema = _schema_for(record, _FIELD_PATHS[agent], (), 0, [_MAX_NODES])
        schema_json = _canonical(
            {"agent": agent, "discriminator": safe_discriminator, "schema": schema}
        )
        signature_id = hashlib.sha256(schema_json.encode("ascii")).hexdigest()
        previous = groups.get(signature_id)
        groups[signature_id] = SchemaGroup(
            signature_id=signature_id,
            agent=agent,
            discriminator=safe_discriminator,
            schema_json=schema_json,
            count=1 if previous is None else previous.count + 1,
        )
    return tuple(groups[key] for key in sorted(groups))
