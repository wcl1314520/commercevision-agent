import runpy
from pathlib import Path

import commercevision_domain
import pytest
from commercevision_contracts import WorkspaceId
from commercevision_domain.workspace_identity import (
    WORKSPACE_ID_PATTERN,
    is_valid_workspace_id,
    validate_workspace_id,
)
from pydantic import BaseModel, ValidationError


class WorkspacePayload(BaseModel):
    workspace_id: WorkspaceId


def _ticket02_migration_path() -> Path:
    return (
        Path(__file__).parents[2]
        / "database"
        / "migrations"
        / "versions"
        / "b1c8e4f2a703_durable_operations_recovery.py"
    )


@pytest.mark.parametrize(
    "workspace_id",
    [
        "A",
        "workspace-._:9",
        "W" * 128,
    ],
)
def test_workspace_identity_contract_accepts_exact_ascii_tokens(
    workspace_id: str,
) -> None:
    assert WORKSPACE_ID_PATTERN == r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    assert validate_workspace_id(workspace_id) == workspace_id
    assert is_valid_workspace_id(workspace_id)


@pytest.mark.parametrize(
    "workspace_id",
    [
        "",
        "w" * 129,
        "-workspace",
        "_workspace",
        ".workspace",
        ":workspace",
        " workspace",
        "workspace ",
        "work\tspace",
        "work\nspace",
        "workspace\n",
        "work\x00space",
        "cafe\u0301",
        "café",
        "工作区",
    ],
)
def test_workspace_identity_contract_rejects_without_normalizing(
    workspace_id: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="workspace_id must match",
    ):
        validate_workspace_id(workspace_id)
    assert not is_valid_workspace_id(workspace_id)


def test_workspace_identity_contract_rejects_non_strings() -> None:
    with pytest.raises(ValueError, match="workspace_id must match"):
        validate_workspace_id(42)  # type: ignore[arg-type]
    assert not is_valid_workspace_id(42)


def test_workspace_pydantic_boundary_preserves_and_enforces_the_same_contract() -> None:
    workspace_id = "Workspace._:-9"
    assert WorkspacePayload(workspace_id=workspace_id).workspace_id == workspace_id
    for invalid_workspace_id in ("workspace\n", "café", "工作区"):
        with pytest.raises(ValidationError):
            WorkspacePayload(workspace_id=invalid_workspace_id)


def test_historical_migration_owns_its_workspace_contract_literal() -> None:
    source = _ticket02_migration_path().read_text(encoding="utf-8")

    assert "from commercevision_domain import WORKSPACE_ID_PATTERN" not in source
    assert '_WORKSPACE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"' in source


def test_historical_migration_ignores_future_runtime_workspace_contract_changes(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        commercevision_domain,
        "WORKSPACE_ID_PATTERN",
        r"^runtime-contract-was-mutated$",
    )

    migration = runpy.run_path(str(_ticket02_migration_path()))
    predicate = migration["_workspace_id_sql_predicate"]("workspace_id")

    assert r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$" in predicate
    assert "runtime-contract-was-mutated" not in predicate
