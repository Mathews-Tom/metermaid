"""Run the explicit, local-only unsupported-schema triage aid."""

from __future__ import annotations

import sys

# The read-only advisor must not create bytecode files while importing its modules.
sys.dont_write_bytecode = True

from metermaid.schema_advisor_runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
