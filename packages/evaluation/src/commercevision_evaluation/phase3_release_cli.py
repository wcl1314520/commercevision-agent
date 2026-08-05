"""Command-line Phase 3 release evidence gate."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .release_acceptance import audit_phase3_release
from .reporting import write_phase3_release_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit CommerceVision Phase 3 release evidence")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--markdown-output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = audit_phase3_release(
            arguments.manifest,
            repository_root=arguments.repository_root,
        )
        write_phase3_release_report(
            report,
            json_path=arguments.json_output,
            markdown_path=arguments.markdown_output,
        )
    except (OSError, ValueError) as error:
        print(f"Phase 3 release evidence rejected: {type(error).__name__}", file=sys.stderr)
        return 2
    print("Phase 3 release acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
