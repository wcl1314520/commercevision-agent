from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from commercevision_persistence.models import MYSQL_DATETIME_FSP, UTCDateTime
from commercevision_persistence.schema import compare_mysql_datetime_precision
from sqlalchemy.dialects.mysql import DATETIME as MYSQL_DATETIME


def test_utc_datetime_normalizes_to_utc_and_restores_awareness() -> None:
    column_type = UTCDateTime()
    source = datetime(2026, 7, 22, 11, 15, 56, 516123, tzinfo=timezone(timedelta(hours=8)))

    bound = column_type.process_bind_param(source, SimpleNamespace(name="mysql"))
    restored = column_type.process_result_value(bound, SimpleNamespace(name="mysql"))

    assert bound == datetime(2026, 7, 22, 3, 15, 56, 516123)
    assert restored == datetime(2026, 7, 22, 3, 15, 56, 516123, tzinfo=UTC)


def test_utc_datetime_rejects_naive_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        UTCDateTime().process_bind_param(
            datetime(2026, 7, 22, 3, 15, 56, 516123),
            SimpleNamespace(name="mysql"),
        )


def test_alembic_comparator_detects_mysql_fractional_second_drift() -> None:
    context = SimpleNamespace(dialect=SimpleNamespace(name="mysql"))
    metadata_type = UTCDateTime()

    assert (
        compare_mysql_datetime_precision(
            context,
            None,
            None,
            MYSQL_DATETIME(),
            metadata_type,
        )
        is True
    )
    assert (
        compare_mysql_datetime_precision(
            context,
            None,
            None,
            MYSQL_DATETIME(fsp=MYSQL_DATETIME_FSP),
            metadata_type,
        )
        is False
    )
