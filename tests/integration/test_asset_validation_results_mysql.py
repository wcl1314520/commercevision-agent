from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from commercevision_domain import (
    AssetValidationResult,
    ValidationStage,
    ValidationVerdict,
)
from commercevision_persistence.assets import AssetRepository
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 26, 10, 30, tzinfo=UTC)


def _result(*, result_id: str) -> AssetValidationResult:
    return AssetValidationResult(
        id=result_id,
        workspace_id="validation-workspace",
        operation_id="019f8a00-0000-7000-8000-000000000602",
        asset_version_id="019f8a00-0000-7000-8000-000000000603",
        asset_object_id="019f8a00-0000-7000-8000-000000000604",
        attempt_number=1,
        stage=ValidationStage.CONTENT_SAFETY,
        validator_name="alibaba-green20220302",
        validator_version="3.2.4",
        policy_version="moderation-policy-v3",
        verdict=ValidationVerdict.REVIEW,
        reason_code="CONTENT_REVIEW_REQUIRED",
        object_provider_version_id="minio-object-version-1",
        object_etag='"etag-1"',
        content_sha256="a" * 64,
        evidence={
            "endpoint": "green-cip.cn-shanghai.aliyuncs.com",
            "labels": [{"code": "risk_label", "confidence": 82.5}],
            "mapping_version": "alibaba-risk-map-v2",
            "outcome": "REVIEW",
            "provider": "alibaba-green20220302",
            "request_id": "request-123",
            "risk_level": "medium",
            "sdk_version": "3.2.4",
            "service": "postImageCheckByVL_ec",
        },
        retention_deadline=NOW + timedelta(hours=72),
        created_at=NOW,
    )


def test_validation_result_repository_is_insert_only_and_workspace_scoped(
    integration_database,
) -> None:
    engine = integration_database.engine
    result = _result(result_id="019f8a00-0000-7000-8000-000000000601")
    with Session(engine) as session:
        session.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        repository = AssetRepository(session)
        repository.add_validation_result(result)
        session.commit()

    with Session(engine) as session:
        repository = AssetRepository(session)
        loaded = repository.list_validation_results(
            workspace_id="validation-workspace",
            asset_version_id=result.asset_version_id,
        )
        exact = repository.get_validation_result(
            workspace_id="validation-workspace",
            asset_version_id=result.asset_version_id,
            operation_id=result.operation_id,
            attempt_number=1,
            stage=ValidationStage.CONTENT_SAFETY,
            validator_name="alibaba-green20220302",
            validator_version="3.2.4",
            policy_version="moderation-policy-v3",
        )
        isolated = repository.list_validation_results(
            workspace_id="another-workspace",
            asset_version_id=result.asset_version_id,
        )

    assert loaded == [result]
    assert exact == result
    assert isolated == []
    assert loaded[0].evidence_dict()["labels"] == [{"code": "risk_label", "confidence": 82.5}]
    assert not hasattr(repository, "save_validation_result")
    assert not hasattr(repository, "delete_validation_result")

    duplicate = _result(result_id="019f8a00-0000-7000-8000-000000000605")
    with pytest.raises(DBAPIError), Session(engine) as session:
        session.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        AssetRepository(session).add_validation_result(duplicate)
        session.commit()

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM asset_validation_results WHERE workspace_id = 'validation-workspace'")
        )
