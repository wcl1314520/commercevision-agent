"""Run pip-audit with deterministic UTF-8 subprocess output."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    return subprocess.call(
        [sys.executable, "-m", "pip_audit", "--skip-editable"],
        env=environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
