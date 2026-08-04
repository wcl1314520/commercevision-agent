"""Command-line release gate for fixed retrieval evaluation suites."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .manifest import load_retrieval_evaluation
from .reporting import write_retrieval_report
from .retrieval import evaluate_retrieval


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the CommerceVision retrieval quality gate")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--profile", required=True, choices=("daily", "release"))
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--markdown-output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = evaluate_retrieval(
            load_retrieval_evaluation(
                arguments.manifest,
                arguments.observations,
                profile=arguments.profile,
            )
        )
        write_retrieval_report(
            report,
            json_path=arguments.json_output,
            markdown_path=arguments.markdown_output,
        )
    except (OSError, ValueError) as error:
        print(f"retrieval evaluation rejected: {type(error).__name__}", file=sys.stderr)
        return 2
    print(f"retrieval evaluation gate: {'PASS' if report.gate.passed else 'FAIL'}")
    return 0 if report.gate.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
