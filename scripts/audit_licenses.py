"""Fail CI on missing or policy-blocked Python dependency licenses."""

from __future__ import annotations

import re
from collections.abc import Iterable
from importlib.metadata import Distribution, distributions

_WORKSPACE_PREFIX = "commercevision-"
_LICENSE_OVERRIDES = {("milvus-lite", "2.4.12"): "Apache-2.0"}
_BLOCKED = re.compile(
    r"GNU\s+(?:AFFERO\s+)?GENERAL\s+PUBLIC\s+LICENSE"
    r"|(?:^|[^A-Z])(?:A?GPL|SSPL|BUSL|EUPL)(?=[^A-Z]|V?\d|$)",
    re.IGNORECASE,
)


def _license_value(distribution: Distribution) -> str:
    metadata = distribution.metadata
    value = metadata.get("License-Expression")
    if not value:
        classifiers = metadata.get_all("Classifier", [])
        value = " OR ".join(
            classifier.removeprefix("License :: ").strip()
            for classifier in classifiers
            if classifier.startswith("License :: ")
        )
    if not value:
        value = metadata.get("License")
    if not value:
        name = (metadata.get("Name") or "").lower()
        version = metadata.get("Version") or ""
        value = _LICENSE_OVERRIDES.get((name, version))
    return " ".join(value.split()) if value else ""


def audit_python_licenses(
    installed: Iterable[Distribution] | None = None,
) -> tuple[str, ...]:
    """Return stable policy findings for installed Python distributions."""

    findings: list[str] = []
    for distribution in installed if installed is not None else distributions():
        name = distribution.metadata.get("Name") or "unknown-distribution"
        license_value = _license_value(distribution)
        if not license_value:
            if not name.lower().startswith(_WORKSPACE_PREFIX):
                findings.append(f"{name}: missing license metadata")
            continue
        blocked = _BLOCKED.search(license_value[:512])
        if blocked is not None:
            display = license_value if len(license_value) <= 160 else f"{license_value[:159]}…"
            findings.append(f"{name}: blocked license {display}")
    return tuple(sorted(findings))


def main() -> int:
    findings = audit_python_licenses()
    if findings:
        print("Python license policy: FAIL")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Python license policy: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
