from __future__ import annotations

from typing import Any, get_protocol_members

import pytest
from commercevision_application.product_brief_ports import (
    ProviderArtifactTargetReadinessQuery,
)
from commercevision_contracts.product_briefs import ProviderArtifactPhysicalTarget
from commercevision_domain import StorageBackend, StorageLocationClass
from commercevision_persistence import SqlAlchemyProviderArtifactTargetReadinessQuery
from sqlalchemy.dialects import mysql


class MappingResult:
    def __init__(self, rows: tuple[dict[str, str], ...]) -> None:
        self._rows = rows

    def mappings(self) -> MappingResult:
        return self

    def all(self) -> tuple[dict[str, str], ...]:
        return self._rows


class RecordingSession:
    def __init__(self, rows: tuple[dict[str, str], ...]) -> None:
        self._rows = rows
        self.statement: Any = None

    def __enter__(self) -> RecordingSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: Any) -> MappingResult:
        self.statement = statement
        return MappingResult(self._rows)


def test_target_readiness_query_port_has_one_narrow_operation() -> None:
    assert get_protocol_members(ProviderArtifactTargetReadinessQuery) == {
        "list_reconciliation_targets"
    }


def test_target_readiness_query_is_one_distinct_bounded_deadline_query() -> None:
    session = RecordingSession(
        (
            {
                "storage_backend": StorageBackend.MINIO.value,
                "location": StorageLocationClass.PROVIDER_RESULT.value,
                "bucket": "provider-results",
            },
            {
                "storage_backend": StorageBackend.OSS.value,
                "location": StorageLocationClass.PROVIDER_RESULT.value,
                "bucket": "provider-results-legacy",
            },
        )
    )
    query = SqlAlchemyProviderArtifactTargetReadinessQuery(  # type: ignore[arg-type]
        lambda: session
    )

    targets = query.list_reconciliation_targets(limit=17)

    assert targets == (
        ProviderArtifactPhysicalTarget(
            storage_backend=StorageBackend.MINIO,
            location=StorageLocationClass.PROVIDER_RESULT,
            bucket="provider-results",
        ),
        ProviderArtifactPhysicalTarget(
            storage_backend=StorageBackend.OSS,
            location=StorageLocationClass.PROVIDER_RESULT,
            bucket="provider-results-legacy",
        ),
    )
    sql = str(
        session.statement.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "MAX_EXECUTION_TIME(5000)" in sql
    assert "DISTINCT" in sql
    assert "state IN ('INTENDED', 'UNKNOWN')" in sql
    assert "ORDER BY" in sql
    assert "LIMIT 18" in sql
    assert "object_key" not in sql


def test_target_readiness_query_fails_closed_above_the_hard_limit() -> None:
    session = RecordingSession(
        tuple(
            {
                "storage_backend": StorageBackend.OSS.value,
                "location": StorageLocationClass.PROVIDER_RESULT.value,
                "bucket": f"provider-results-{index}",
            }
            for index in range(3)
        )
    )
    query = SqlAlchemyProviderArtifactTargetReadinessQuery(  # type: ignore[arg-type]
        lambda: session
    )

    with pytest.raises(
        RuntimeError,
        match="provider artifact reconciliation targets exceed the readiness bound",
    ):
        query.list_reconciliation_targets(limit=2)

    sql = str(
        session.statement.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LIMIT 3" in sql


def test_target_readiness_query_rejects_a_caller_limit_above_the_configured_bound() -> None:
    query = SqlAlchemyProviderArtifactTargetReadinessQuery(  # type: ignore[arg-type]
        lambda: RecordingSession(())
    )

    with pytest.raises(
        ValueError,
        match="between 1 and 17",
    ):
        query.list_reconciliation_targets(limit=18)
