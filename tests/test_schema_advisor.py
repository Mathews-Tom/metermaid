"""Privacy and fail-closed contracts for the developer-only schema advisor."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from metermaid.schema_advisor import (
    AdvisorConfig,
    AdvisorError,
    SchemaGroup,
    group_records,
    load_config,
    validate_config,
)

_VALID_CONFIG = """[schema_advisor]
enabled = true
endpoint = "http://127.0.0.1:11434/api/generate"
model = "configured-model-canary"
timeout_seconds = 30
"""


def _config(path: Path, content: str = _VALID_CONFIG) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _group(record: Mapping[str, object], *, agent: str = "omp") -> SchemaGroup:
    return group_records([(agent, "message", record)])[0]


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _fields(schema: object) -> dict[str, object]:
    return _object(_object(schema)["fields"])


def _root(group: SchemaGroup) -> dict[str, object]:
    document = _object(json.loads(group.schema_json))
    return _fields(document["schema"])


def test_explicit_config_takes_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _config(tmp_path / "xdg/metermaid/schema-advisor.toml", "not valid TOML")
    explicit = _config(tmp_path / "explicit.toml")
    config = load_config(explicit)
    assert config.endpoint == "http://127.0.0.1:11434/api/generate"
    assert config.model == "configured-model-canary"
    assert config.timeout_seconds == 30
    assert "configured-model-canary" not in repr(config)
    assert config.endpoint not in repr(config)


def test_xdg_config_precedes_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _config(tmp_path / "home/.config/metermaid/schema-advisor.toml", "invalid")
    _config(tmp_path / "xdg/metermaid/schema-advisor.toml")
    assert load_config().timeout_seconds == 30


def test_unset_xdg_uses_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    _config(tmp_path / ".config/metermaid/schema-advisor.toml")
    assert load_config().model == "configured-model-canary"


def test_empty_xdg_is_not_treated_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    _config(tmp_path / "home/.config/metermaid/schema-advisor.toml", "invalid")
    _config(tmp_path / "metermaid/schema-advisor.toml")
    assert load_config().timeout_seconds == 30


def test_explicit_missing_config_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _config(tmp_path / "metermaid/schema-advisor.toml")
    with pytest.raises(AdvisorError, match="^needs-human-review$") as error:
        load_config(tmp_path / "private-path-canary.toml")
    assert "private-path-canary" not in repr(error.value)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "private-secret-canary = [",
        _VALID_CONFIG.replace("enabled = true", "enabled = false"),
        _VALID_CONFIG.replace("enabled = true", "enabled = 1"),
        _VALID_CONFIG.replace("enabled = true", 'enabled = "true"'),
        _VALID_CONFIG.replace("enabled = true\n", ""),
        _VALID_CONFIG.replace('model = "configured-model-canary"', 'model = ""'),
        _VALID_CONFIG.replace('model = "configured-model-canary"', 'model = "   "'),
        _VALID_CONFIG.replace('model = "configured-model-canary"', "model = 42"),
        _VALID_CONFIG.replace("timeout_seconds = 30", "timeout_seconds = true"),
        _VALID_CONFIG.replace("timeout_seconds = 30", "timeout_seconds = 1.0"),
        _VALID_CONFIG.replace("timeout_seconds = 30", "timeout_seconds = 0"),
        _VALID_CONFIG.replace("timeout_seconds = 30", "timeout_seconds = 121"),
        _VALID_CONFIG.replace("timeout_seconds = 30\n", ""),
        _VALID_CONFIG.replace(
            'endpoint = "http://127.0.0.1:11434/api/generate"', "endpoint = 1"
        ),
        _VALID_CONFIG + 'headers = {Authorization = "private-secret-canary"}\n',
        _VALID_CONFIG + '[other]\nsecret = "private-secret-canary"\n',
        'schema_advisor = "private-secret-canary"\n',
    ],
)
def test_invalid_config_has_only_static_errors(tmp_path: Path, content: str) -> None:
    with pytest.raises(AdvisorError) as error:
        load_config(_config(tmp_path / "private-path-canary.toml", content))
    assert str(error.value) == "needs-human-review"
    assert "canary" not in repr(error.value)


def test_unreadable_utf8_config_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "private-path-canary.toml"
    path.write_bytes(b"\xff\xfeprivate-secret-canary")
    with pytest.raises(AdvisorError, match="^needs-human-review$"):
        load_config(path)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1/api/generate",
        "http://localhost:11434/api/generate",
        "http://LOCALHOST:11434/api/generate",
        "http://[::1]:11434/api/generate",
        "http://127.0.0.1:1/api/generate",
        "http://127.0.0.1:65535/api/generate",
    ],
)
@pytest.mark.parametrize("timeout", [1, 120])
def test_loopback_configs_are_accepted(endpoint: str, timeout: int) -> None:
    validate_config(AdvisorConfig(endpoint, "local-model", timeout))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1/api/generate",
        "http://example.com/api/generate",
        "http://127.0.0.2/api/generate",
        "http://127.1/api/generate",
        "http://2130706433/api/generate",
        "http://localhost.example.com/api/generate",
        "http://localhost./api/generate",
        "http://[::ffff:127.0.0.1]/api/generate",
        "http://[0:0:0:0:0:0:0:1]/api/generate",
        "http://localhost@127.0.0.1/api/generate",
        "http://private-secret-canary:password@localhost/api/generate",
        "http://localhost/api/generate?",
        "http://localhost/api/generate?private-secret-canary",
        "http://localhost/api/generate#",
        "http://localhost/api/generate#private-secret-canary",
        "http://localhost/api/generate/",
        "http://localhost/api/%67enerate",
        "http://localhost/api/chat",
        "http://localhost:/api/generate",
        "http://localhost:0/api/generate",
        "http://localhost:-1/api/generate",
        "http://localhost:65536/api/generate",
        "http://localhost:not-a-port/api/generate",
        "http://localhost:１２/api/generate",
        "http://[::1/api/generate",
        "http://localhost\\private-secret-canary/api/generate",
        " http://localhost/api/generate",
        "http://local\nhost/api/generate",
        "http://localhost/api/generate\t",
        "http://localhost/api/generate\x00",
        "http://localhost/api/generate\x7f",
    ],
)
def test_bad_endpoints_fail_loaded_and_direct_configs(
    tmp_path: Path, endpoint: str
) -> None:
    with pytest.raises(AdvisorError, match="^needs-human-review$"):
        validate_config(AdvisorConfig(endpoint, "local-model", 30))
    content = _VALID_CONFIG.replace(
        '"http://127.0.0.1:11434/api/generate"', json.dumps(endpoint)
    )
    with pytest.raises(AdvisorError, match="^needs-human-review$"):
        load_config(_config(tmp_path / "config.toml", content))


@pytest.mark.parametrize("timeout", [True, False, 0, 121, 1.0, "30", None])
def test_direct_config_timeout_is_strict(timeout: object) -> None:
    with pytest.raises(AdvisorError, match="^needs-human-review$"):
        validate_config(
            AdvisorConfig(
                "http://localhost/api/generate", "local-model", cast(int, timeout)
            )
        )


def test_all_source_values_and_unknown_names_are_removed() -> None:
    canaries = [
        "prompt_canary",
        "/Users/private/source-path-canary.jsonl",
        "session-canary",
        "2026-10-27T12:34:56Z",
        "private-model-canary",
        "tool-argument-canary",
        "tool-result-canary",
        "nested-discriminator-canary",
        "ascii_field_canary",
    ]
    record: dict[str, object] = {
        "type": canaries[0],
        canaries[8]: canaries[1],
        "sessionId": canaries[2],
        "timestamp": canaries[3],
        "message": {
            "model": canaries[4],
            "toolName": canaries[5],
            "content": [{"type": canaries[7], "result": canaries[6]}],
            "usage": {"input": 123456789, "output": 987654321},
        },
    }
    group = group_records([("omp", canaries[7], record)])[0]
    for canary in canaries:
        assert canary not in group.schema_json
        assert canary not in repr(group)
    assert "123456789" not in group.schema_json
    assert "987654321" not in group.schema_json
    assert group.discriminator == "unknown"
    assert "<redacted-field>" in group.schema_json
    assert "<redacted-field-" not in group.schema_json


def test_values_unknown_names_and_repeated_array_shapes_do_not_split_groups() -> None:
    first: dict[str, object] = {
        "type": "first-secret",
        "timestamp": "first-time",
        "first_unknown_name": [1, {"first_nested_name": "secret"}],
        "message": {"model": "private-model-one", "usage": {"input": 10}},
    }
    second: dict[str, object] = {
        "second_unknown_name": [{"other_nested_name": "different"}, 2, 3, 4],
        "timestamp": "second-time",
        "message": {"usage": {"input": 200}, "model": "private-model-two"},
        "type": "second-secret",
    }
    groups = group_records(
        [
            ("omp", "compact-secret-one", first),
            ("omp", "compact-secret-two", second),
        ]
    )
    assert len(groups) == 1
    assert groups[0].count == 2
    assert groups[0].discriminator == "unknown"
    assert (
        groups[0].signature_id
        == hashlib.sha256(groups[0].schema_json.encode("ascii")).hexdigest()
    )


def test_multiple_unknown_keys_preserve_type_union_without_counts() -> None:
    first = _group({"secret_a": 1, "secret_b": "s", "secret_c": True})
    repeated = _group({"x": True, "y": "t", "z": 2, "another": 3, "again": True})
    missing_type = _group({"x": True, "y": "t"})
    assert first.signature_id == repeated.signature_id
    assert first.signature_id != missing_type.signature_id
    redacted = _object(_root(first)["<redacted-field>"])
    assert redacted["anyOf"] == ["boolean", "integer", "string"]


def test_json_types_remain_structurally_distinct() -> None:
    values: list[object] = [None, True, 1, 1.5, "s", [], {}]
    records: list[tuple[str, str, Mapping[str, object]]] = [
        ("omp", "message", {"message": {"usage": {"input": value}}}) for value in values
    ]
    assert len(group_records(records)) == 7


def test_array_item_sets_have_no_positions_lengths_or_values() -> None:
    first = _group({"unknown": [None, 1, 1.2, True, "secret", [], {}]})
    second = _group({"different": [{}, [], "other", False, 2.3, 3, None, None, 4]})
    assert first.signature_id == second.signature_id
    unknown = _object(_root(first)["<redacted-field>"])
    schemas = cast(list[object], unknown["anyOf"])
    array = _object(schemas[0])
    assert array["type"] == "array"
    assert len(cast(list[object], array["items"])) == 7
    assert set(array) == {"type", "items"}


def test_known_names_are_allowed_only_on_reviewed_paths() -> None:
    group = _group(
        {
            "input": 10,
            "usage": {"input": 10},
            "message": {
                "usage": {"input": 10, "unexpected_parent": {"output": 20}},
                "unexpected_parent": {"usage": {"input": 10}},
            },
        }
    )
    root = _root(group)
    assert "input" not in root
    assert "usage" not in root
    message = _fields(root["message"])
    usage = _fields(message["usage"])
    assert usage["input"] == "integer"
    unknown_usage = _object(usage["<redacted-field>"])
    nested = cast(list[object], unknown_usage["anyOf"])
    assert "output" not in _fields(nested[0])
    unknown_message = _object(message["<redacted-field>"])
    assert '"usage"' not in json.dumps(unknown_message)
    assert '"input"' not in json.dumps(unknown_message)


def test_array_boundary_does_not_reuse_object_field_permissions() -> None:
    root = _root(_group({"message": [{"usage": {"input": 1}}]}))
    message = _object(root["message"])
    items = cast(list[object], message["items"])
    assert "usage" not in _fields(items[0])
    assert '"input"' not in json.dumps(items)


@pytest.mark.parametrize(
    ("agent", "discriminator", "record", "counter"),
    [
        (
            "claude-code",
            "assistant",
            {"message": {"usage": {"input_tokens": 1}}},
            "input_tokens",
        ),
        (
            "codex",
            "token_count",
            {"payload": {"info": {"total_token_usage": {"input_tokens": 1}}}},
            "input_tokens",
        ),
        ("pi", "message", {"message": {"usage": {"input": 1}}}, "input"),
        ("omp", "message", {"message": {"usage": {"input": 1}}}, "input"),
    ],
)
def test_pilot_counter_presence_is_part_of_signature(
    agent: str, discriminator: str, record: Mapping[str, object], counter: str
) -> None:
    group = group_records([(agent, discriminator, record)])[0]
    assert group.agent == agent
    assert group.discriminator == discriminator
    assert f'"{counter}":"integer"' in group.schema_json
    absent = group_records([(agent, discriminator, {})])[0]
    assert group.signature_id != absent.signature_id


def test_known_discriminators_and_agents_partition_groups_deterministically() -> None:
    records: list[tuple[str, str, Mapping[str, object]]] = [
        ("omp", "message", {}),
        ("pi", "message", {}),
        ("codex", "token_count", {}),
        ("codex", "event_msg", {}),
        ("claude-code", "assistant", {}),
    ]
    assert len(group_records(records)) == 5
    assert group_records(records) == group_records(reversed(records))
    assert group_records([]) == ()


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        b"private-secret-canary",
        (1, 2),
        {1, 2},
        object(),
    ],
)
def test_non_json_values_fail_without_content(value: object) -> None:
    with pytest.raises(AdvisorError) as error:
        _group({"private-field-canary": value})
    assert str(error.value) == "needs-human-review"
    assert "canary" not in repr(error.value)


def test_non_string_object_keys_are_rejected() -> None:
    record = cast(Mapping[str, object], {1: "private-secret-canary"})
    with pytest.raises(AdvisorError, match="^needs-human-review$"):
        _group(record)


def test_non_pilot_agents_are_rejected_without_echo() -> None:
    with pytest.raises(AdvisorError, match="^needs-human-review$") as error:
        _group({}, agent="private-agent-canary")
    assert "private-agent-canary" not in repr(error.value)


def test_non_object_record_is_rejected() -> None:
    with pytest.raises(AdvisorError, match="^needs-human-review$"):
        _group(cast(Mapping[str, object], ["private-secret-canary"]))


def test_excessive_depth_and_cycles_fail_statically() -> None:
    deep: dict[str, object] = {}
    cursor = deep
    for _ in range(100):
        nested: dict[str, object] = {}
        cursor["private-field-canary"] = nested
        cursor = nested
    cyclic: dict[str, object] = {}
    cyclic["private-field-canary"] = cyclic
    for record in (deep, cyclic):
        with pytest.raises(AdvisorError, match="^needs-human-review$"):
            _group(record)


def test_excessive_width_fails_statically() -> None:
    with pytest.raises(AdvisorError, match="^needs-human-review$"):
        _group({"private-field-canary": [None] * 100_001})
