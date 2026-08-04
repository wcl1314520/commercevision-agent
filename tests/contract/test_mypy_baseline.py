from __future__ import annotations

import json
import runpy
import tomllib
from pathlib import Path

check_mypy = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts/check_mypy_baseline.py"),
    run_name="commercevision_mypy_baseline",
)
normalize_diagnostics = check_mypy["normalize_diagnostics"]
compare_diagnostics = check_mypy["compare_diagnostics"]
ROOT = Path(__file__).parents[2]


def _line(path: str, message: str) -> str:
    return json.dumps(
        {
            "file": path,
            "line": 7,
            "column": 3,
            "end_line": 7,
            "end_column": 8,
            "message": message,
            "hint": None,
            "code": "assignment",
            "severity": "error",
        }
    )


def test_mypy_baseline_normalizes_paths_and_matches_exact_diagnostics() -> None:
    baseline = normalize_diagnostics([_line("packages\\domain\\model.py", "bad assignment")])
    current = normalize_diagnostics([_line("packages/domain/model.py", "bad assignment")])

    assert current == baseline
    assert compare_diagnostics(current, baseline) == ()


def test_mypy_baseline_reports_new_and_resolved_diagnostics() -> None:
    baseline = normalize_diagnostics([_line("packages/old.py", "old error")])
    current = normalize_diagnostics([_line("packages/new.py", "new error")])

    assert compare_diagnostics(current, baseline) == (
        "new type error: packages/new.py:7:3 [assignment] new error",
        "resolved baseline entry: packages/old.py:7:3 [assignment] old error",
    )


def test_mypy_baseline_targets_the_linux_release_platform() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert configuration["tool"]["mypy"]["platform"] == "linux"
