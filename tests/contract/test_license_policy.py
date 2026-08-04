from __future__ import annotations

import runpy
from email.message import Message
from pathlib import Path

audit_python_licenses = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts/audit_licenses.py"),
    run_name="commercevision_license_audit",
)["audit_python_licenses"]


class _Distribution:
    def __init__(self, name: str, **metadata: str) -> None:
        value = Message()
        value["Name"] = name
        for key, item in metadata.items():
            value[key.replace("_", "-")] = item
        self.metadata = value


def test_python_license_policy_allows_workspace_and_permissive_dependencies() -> None:
    findings = audit_python_licenses(
        [
            _Distribution("commercevision-domain"),
            _Distribution("httpx", License_Expression="BSD-3-Clause"),
            _Distribution("certifi", License="MPL-2.0"),
            _Distribution("native-runtime", License_Expression="LGPLv3+"),
        ]
    )

    assert findings == ()


def test_python_license_policy_rejects_unknown_and_blocked_external_dependencies() -> None:
    findings = audit_python_licenses(
        [
            _Distribution("mystery"),
            _Distribution("network-service", License_Expression="GPLv3+"),
            _Distribution("legacy-service", License="GNU General Public License v2"),
        ]
    )

    assert findings == (
        "legacy-service: blocked license GNU General Public License v2",
        "mystery: missing license metadata",
        "network-service: blocked license GPLv3+",
    )
