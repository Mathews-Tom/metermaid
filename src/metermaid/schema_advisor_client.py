"""Bounded, loopback-only transport for the opt-in schema advisor."""

from __future__ import annotations

import http.client
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlsplit

from metermaid.schema_advisor import AdvisorConfig, SchemaGroup, validate_config

Category = Literal[
    "expected-envelope",
    "non-usage-message",
    "possible-usage-shape",
    "needs-human-review",
]
_CATEGORIES = frozenset(
    (
        "expected-envelope",
        "non-usage-message",
        "possible-usage-shape",
        "needs-human-review",
    )
)
_MAX_RESPONSE_BYTES = 65_536
_INSTRUCTION = (
    "Classify the following value-free schema. Return only one JSON object with "
    "exactly one field named category. Its value must be one of: "
    "expected-envelope, non-usage-message, possible-usage-shape, needs-human-review. "
    "Do not return rationale or other fields. Schema and aggregate count follow:\n"
)


@dataclass(frozen=True)
class Advice:
    group: SchemaGroup
    category: Category
    failed: bool


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("needs-human-review")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError("needs-human-review")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("needs-human-review")
    return number


def _decode_json(value: str | bytes) -> object:
    return cast(
        object,
        json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        ),
    )


def _category(body: bytes) -> Category:
    envelope = _decode_json(body)
    if not isinstance(envelope, dict):
        raise ValueError("needs-human-review")
    response = envelope.get("response")
    if not isinstance(response, str):
        raise ValueError("needs-human-review")
    result = _decode_json(response)
    if not isinstance(result, dict) or set(result) != {"category"}:
        raise ValueError("needs-human-review")
    category = result["category"]
    if not isinstance(category, str) or category not in _CATEGORIES:
        raise ValueError("needs-human-review")
    return cast(Category, category)


def advise_groups(
    config: AdvisorConfig, groups: Sequence[SchemaGroup]
) -> tuple[Advice, ...]:
    """Request each sanitized group once, preserving the supplied group order.

    Invalid configuration raises the shared safe configuration error before any
    connection. Transport and response failures become structural advice only.
    """
    validate_config(config)
    endpoint = urlsplit(config.endpoint)
    host = "::1" if endpoint.hostname == "::1" else "127.0.0.1"
    advice: list[Advice] = []
    for group in groups:
        connection = http.client.HTTPConnection(
            host, endpoint.port or 80, timeout=config.timeout_seconds
        )
        category: Category = "needs-human-review"
        failed = True
        try:
            payload = json.dumps(
                {
                    "model": config.model,
                    "stream": False,
                    "think": False,
                    "format": "json",
                    "prompt": _INSTRUCTION
                    + json.dumps(
                        {"schema": json.loads(group.schema_json), "count": group.count},
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                },
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            connection.request(
                "POST", "/api/generate", payload, {"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            if 200 <= response.status < 300:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(body) <= _MAX_RESPONSE_BYTES:
                    category = _category(body)
                    failed = False
        except (OSError, http.client.HTTPException, ValueError, RecursionError):
            category = "needs-human-review"
            failed = True
        finally:
            connection.close()
        advice.append(Advice(group, category, failed))
    return tuple(advice)
