from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from commercevision_persistence.assets import SqlAlchemyAssetUnitOfWork
from sqlalchemy.exc import OperationalError


def test_retention_commit_fence_rolls_back_when_clock_query_fails() -> None:
    session = Mock()
    database_error = OperationalError(
        "SELECT retention_deadline, UTC_TIMESTAMP(6)",
        {},
        TimeoutError("database clock unavailable"),
    )
    session.execute.side_effect = database_error
    unit_of_work = SqlAlchemyAssetUnitOfWork(lambda: session)  # type: ignore[arg-type]
    unit_of_work.__enter__()

    try:
        with pytest.raises(OperationalError) as captured:
            unit_of_work.commit_before_retention_deadline(
                workspace_id="workspace-1",
                asset_id="asset-1",
                retention_deadline=datetime.now(UTC) + timedelta(hours=1),
                clock=lambda: datetime.now(UTC),
            )

        assert captured.value is database_error
        session.rollback.assert_called_once_with()
    finally:
        unit_of_work.__exit__(None, None, None)
