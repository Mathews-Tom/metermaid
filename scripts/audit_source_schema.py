"""Print a selected JSONL source's schema without writing source data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from metermaid.schema_audit import audit_json_lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="local JSONL source to inspect")
    args = parser.parse_args()
    return audit_json_lines(args.source, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
