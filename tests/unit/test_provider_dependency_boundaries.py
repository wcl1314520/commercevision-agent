from __future__ import annotations

import ast
import tomllib
from pathlib import Path


def test_provider_adapters_depend_on_contracts_not_domain_implementations() -> None:
    provider_root = (
        Path(__file__).parents[2] / "packages" / "providers" / "src" / "commercevision_providers"
    )
    violations: list[str] = []

    for source_path in sorted(provider_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (
                    node.module == "commercevision_domain"
                    or node.module.startswith("commercevision_domain.")
                )
            ):
                violations.append(f"{source_path.name}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "commercevision_domain" or alias.name.startswith(
                        "commercevision_domain."
                    ):
                        violations.append(f"{source_path.name}:{node.lineno}")

    assert violations == []

    provider_project = tomllib.loads(
        (provider_root.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = provider_project["project"]["dependencies"]
    assert all(not dependency.startswith("commercevision-domain") for dependency in dependencies)
