"""Contract tests for the reproducible development environment."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
GATE = "uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest"


def test_development_tools_and_python_311_target_are_declared() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    development_dependencies = pyproject["dependency-groups"]["dev"]
    dependency_names = {
        dependency.partition(">")[0] for dependency in development_dependencies
    }

    assert {"mypy", "pytest", "ruff"} <= dependency_names
    assert pyproject["project"]["requires-python"] == ">=3.11"
    assert pyproject["tool"]["mypy"]["python_version"] == "3.11"
    assert pyproject["tool"]["ruff"]["target-version"] == "py311"


def test_ci_syncs_all_groups_before_running_the_repository_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "uv sync --all-groups" in workflow
    assert GATE in workflow
