"""Command-line Phase 2 release evidence gate."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .release_acceptance import audit_phase2_release
from .reporting import write_phase2_release_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit CommerceVision Phase 2 release evidence")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--markdown-output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = audit_phase2_release(
            arguments.manifest,
            repository_root=arguments.repository_root,
        )
        write_phase2_release_report(
            report,
            json_path=arguments.json_output,
            markdown_path=arguments.markdown_output,
        )
    except (OSError, ValueError) as error:
        print(f"Phase 2 release evidence rejected: {type(error).__name__}", file=sys.stderr)
        return 2
    print("Phase 2 release acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
