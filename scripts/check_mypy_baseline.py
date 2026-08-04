"""Run full-workspace Mypy and reject any diagnostic drift."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import mypy.version

_SCHEMA_VERSION = "commercevision.mypy-baseline.v1"
_MAX_BASELINE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, order=True, slots=True)
class Diagnostic:
    path: str
    line: int
    column: int
    code: str
    severity: str
    message: str

    def summary(self) -> str:
        return f"{self.path}:{self.line}:{self.column} [{self.code}] {self.message}"


def normalize_diagnostics(lines: Iterable[str]) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Mypy emitted a non-JSON diagnostic") from error
        if not isinstance(item, dict):
            raise ValueError("Mypy diagnostic must be an object")
        path = item.get("file")
        message = item.get("message")
        code = item.get("code") or "unknown"
        severity = item.get("severity") or "error"
        line_number = item.get("line")
        column = item.get("column")
        if (
            not isinstance(path, str)
            or not isinstance(message, str)
            or not isinstance(code, str)
            or not isinstance(severity, str)
            or isinstance(line_number, bool)
            or not isinstance(line_number, int)
            or isinstance(column, bool)
            or not isinstance(column, int)
        ):
            raise ValueError("Mypy diagnostic has an invalid shape")
        diagnostics.append(
            Diagnostic(
                path=path.replace("\\", "/"),
                line=line_number,
                column=column,
                code=code,
                severity=severity,
                message=message,
            )
        )
    return tuple(sorted(diagnostics))


def compare_diagnostics(
    current: Sequence[Diagnostic],
    baseline: Sequence[Diagnostic],
) -> tuple[str, ...]:
    current_counts = Counter(current)
    baseline_counts = Counter(baseline)
    findings = [
        f"new type error: {diagnostic.summary()}"
        for diagnostic in sorted((current_counts - baseline_counts).elements())
    ]
    findings.extend(
        f"resolved baseline entry: {diagnostic.summary()}"
        for diagnostic in sorted((baseline_counts - current_counts).elements())
    )
    return tuple(findings)


def _reject_duplicate_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ValueError("Mypy baseline contains duplicate JSON keys")
        value[key] = item
    return value


def _diagnostic_identity(diagnostics: Sequence[Diagnostic]) -> tuple[int, str]:
    payload = "\n".join(diagnostic.summary() for diagnostic in diagnostics).encode("utf-8")
    return len(diagnostics), hashlib.sha256(payload).hexdigest()


def _load_baseline(path: Path) -> tuple[int, str]:
    if path.stat().st_size > _MAX_BASELINE_BYTES:
        raise ValueError("Mypy baseline exceeds the size limit")
    value = json.loads(path.read_bytes(), object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "mypy_version",
        "diagnostic_count",
        "diagnostics_sha256",
    }:
        raise ValueError("Mypy baseline has an invalid schema")
    if value["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("Mypy baseline schema version is unsupported")
    if value["mypy_version"] != mypy.version.__version__:
        raise ValueError("Mypy baseline version does not match the locked checker")
    count = value["diagnostic_count"]
    digest = value["diagnostics_sha256"]
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise ValueError("Mypy baseline diagnostic identity is invalid")
    return count, digest


def _write_baseline(path: Path, diagnostics: Sequence[Diagnostic]) -> None:
    count, digest = _diagnostic_identity(diagnostics)
    content = (
        json.dumps(
            {
                "schema_version": _SCHEMA_VERSION,
                "mypy_version": mypy.version.__version__,
                "diagnostic_count": count,
                "diagnostics_sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_mypy() -> tuple[Diagnostic, ...]:
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--output=json", "packages", "services", "scripts"],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
    )
    if completed.returncode not in (0, 1):
        raise RuntimeError("Mypy failed before producing diagnostics")
    if completed.stderr.strip():
        raise RuntimeError("Mypy wrote unexpected stderr output")
    return normalize_diagnostics(completed.stdout.splitlines())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check full-workspace Mypy diagnostic baseline")
    parser.add_argument("--baseline", default=".mypy-baseline.json")
    parser.add_argument("--write-baseline", action="store_true")
    arguments = parser.parse_args(argv)
    baseline_path = Path(arguments.baseline).resolve()
    try:
        diagnostics = _run_mypy()
        if arguments.write_baseline:
            _write_baseline(baseline_path, diagnostics)
            print(f"Mypy baseline wrote {len(diagnostics)} diagnostics")
            return 0
        expected_identity = _load_baseline(baseline_path)
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        print(f"Mypy baseline rejected: {type(error).__name__}", file=sys.stderr)
        return 2
    current_identity = _diagnostic_identity(diagnostics)
    if current_identity != expected_identity:
        print("Mypy baseline: FAIL", file=sys.stderr)
        print(
            f"- expected {expected_identity[0]} diagnostics / {expected_identity[1]}",
            file=sys.stderr,
        )
        print(
            f"- current {current_identity[0]} diagnostics / {current_identity[1]}",
            file=sys.stderr,
        )
        return 1
    print(f"Mypy baseline: PASS ({len(diagnostics)} known diagnostics, no drift)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
