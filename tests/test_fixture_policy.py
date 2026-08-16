"""Tests for the reviewed-fixture and non-persisting audit contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from metermaid.schema_audit import audit_json_lines
from tests.fixture_helpers import REDACTION_MARKER, redacted_record

ROOT = Path(__file__).parent.parent
AUDIT_SCRIPT = ROOT / "scripts" / "audit_source_schema.py"
FIXTURE_POLICY = ROOT / "tests" / "fixtures" / "POLICY.md"


def _file_snapshot(directory: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_redacted_record_replaces_every_value_with_the_review_marker() -> None:
    assert redacted_record(["prompt", "tool_result"]) == {
        "prompt": REDACTION_MARKER,
        "tool_result": REDACTION_MARKER,
    }


@pytest.mark.parametrize("field_names", [[], [""]])
def test_redacted_record_rejects_missing_field_names(field_names: list[str]) -> None:
    with pytest.raises(ValueError):
        redacted_record(field_names)


def test_fixture_policy_is_present() -> None:
    assert FIXTURE_POLICY.read_text().startswith("# Fixture policy")


def test_audit_emits_only_schema_without_writing_source_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "selected-source.jsonl"
    secret_prompt = "never-persist-this-source-prompt"
    secret_path = "/private/project/raw-session.jsonl"
    source.write_text(
        json.dumps(
            {
                "prompt": secret_prompt,
                "project_path": secret_path,
                "tool_result": {secret_path: {"raw": secret_prompt}},
                "usage": {"input_tokens": 12},
            }
        )
        + "\n"
    )
    before = _file_snapshot(tmp_path)
    output = StringIO()

    assert audit_json_lines(source, output) == 0

    after = _file_snapshot(tmp_path)
    report = output.getvalue()
    assert after == before
    assert secret_prompt not in report
    assert secret_path not in report
    assert str(source) not in report
    assert json.loads(report) == {
        "record": 1,
        "schema": {
            "fields": {
                "project_path": "string",
                "prompt": "string",
                "tool_result": {
                    "fields": {
                        "<redacted-field-1>": {
                            "fields": {"raw": "string"},
                            "type": "object",
                        }
                    },
                    "type": "object",
                },
                "usage": {
                    "fields": {"input_tokens": "integer"},
                    "type": "object",
                },
            },
            "type": "object",
        },
    }


def test_audit_rejects_bad_records_without_creating_a_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "bad-source.jsonl"
    oversized_integer = "9" * 5000
    source.write_text(
        '["unreviewed source value"]\n'
        "{malformed}\n"
        f'{{"value": {oversized_integer}}}\n'
        '{"ok": true}\n'
    )
    before = _file_snapshot(tmp_path)
    output = StringIO()

    assert audit_json_lines(source, output) == 1

    assert _file_snapshot(tmp_path) == before
    assert output.getvalue() == (
        "ERROR record 1 at line 1: expected JSON object\n"
        "ERROR record 2 at line 2: malformed JSON\n"
        "ERROR record 3 at line 3: malformed JSON\n"
        '{"record": 4, "schema": {"fields": {"ok": "boolean"}, "type": "object"}}\n'
    )


def test_audit_reports_unreadable_source_without_path(tmp_path: Path) -> None:
    source = tmp_path / "private-source.jsonl"
    before = _file_snapshot(tmp_path)
    output = StringIO()

    assert audit_json_lines(source, output) == 1

    assert output.getvalue() == "ERROR source is unreadable\n"
    assert str(source) not in output.getvalue()
    assert _file_snapshot(tmp_path) == before


def test_audit_array_schemas_are_canonical_across_source_order(tmp_path: Path) -> None:
    source = tmp_path / "array-source.jsonl"
    first_record: dict[str, list[object]] = {
        "items": [2, True, None, {"b": 1}, {"a": "text"}, {"b": 2}, []]
    }
    second_record: dict[str, list[object]] = {
        "items": [[], {"b": 2}, {"a": "text"}, {"b": 1}, None, True, 2]
    }
    first_output = StringIO()
    second_output = StringIO()

    source.write_text(json.dumps(first_record) + "\n")
    assert audit_json_lines(source, first_output) == 0
    source.write_text(json.dumps(second_record) + "\n")
    assert audit_json_lines(source, second_output) == 0

    assert first_output.getvalue() == second_output.getvalue()
    items = json.loads(first_output.getvalue())["schema"]["fields"]["items"]["items"]
    assert len(items) == 6


def test_local_audit_command_reports_unreadable_sources_on_stdout(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private-source.jsonl"
    before = _file_snapshot(tmp_path)

    completed = subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), str(source)],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == ""
    assert completed.stdout == "ERROR source is unreadable\n"
    assert str(source) not in completed.stdout
    assert _file_snapshot(tmp_path) == before


def test_local_audit_command_uses_stdout_without_echoing_the_source_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text('{"session":"local-only"}\n')
    before = _file_snapshot(tmp_path)

    completed = subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), str(source)],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert str(source) not in completed.stdout
    assert "local-only" not in completed.stdout
    assert _file_snapshot(tmp_path) == before
