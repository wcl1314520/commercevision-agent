"""Persist the originating ProductBrief analysis trace."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c8e7f3b219"
down_revision: str | None = "f2a7c9d1e406"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABILITY_TRIGGER = "trg_product_brief_analysis_requests_no_update"


def _unrepresentable_provider_artifact_count(
    connection: sa.Connection,
) -> int:
    return int(
        connection.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM product_brief_provider_artifacts AS artifact
                WHERE artifact.state <> 'STORED'
                    OR (
                        artifact.kind = 'REQUEST'
                        AND NOT EXISTS (
                            SELECT 1
                            FROM product_brief_provider_calls AS calls
                            WHERE calls.request_artifact_id = artifact.id
                                AND calls.workspace_id = artifact.workspace_id
                                AND calls.product_brief_id =
                                    artifact.product_brief_id
                                AND calls.operation_id = artifact.operation_id
                                AND calls.operation_attempt =
                                    artifact.operation_attempt
                                AND calls.call_index = artifact.call_index
                                AND calls.request_artifact_storage_backend
                                    <=> artifact.storage_backend
                                AND calls.request_artifact_location
                                    <=> artifact.location
                                AND calls.request_artifact_bucket
                                    <=> artifact.bucket
                                AND calls.request_artifact_key
                                    <=> artifact.object_key
                                AND calls.request_artifact_provider_version_id
                                    <=> artifact.provider_version_id
                                AND calls.request_artifact_etag <=> artifact.etag
                                AND calls.request_artifact_sha256
                                    <=> artifact.expected_sha256
                                AND calls.request_artifact_byte_size
                                    <=> artifact.expected_byte_size
                                AND calls.retention_class
                                    <=> artifact.retention_class
                                AND calls.retention_deadline
                                    <=> artifact.retention_deadline
                        )
                    )
                    OR (
                        artifact.kind = 'RESPONSE'
                        AND NOT EXISTS (
                            SELECT 1
                            FROM product_brief_provider_calls AS calls
                            WHERE calls.response_artifact_id = artifact.id
                                AND calls.workspace_id = artifact.workspace_id
                                AND calls.product_brief_id =
                                    artifact.product_brief_id
                                AND calls.operation_id = artifact.operation_id
                                AND calls.operation_attempt =
                                    artifact.operation_attempt
                                AND calls.call_index = artifact.call_index
                                AND calls.response_artifact_storage_backend
                                    <=> artifact.storage_backend
                                AND calls.response_artifact_location
                                    <=> artifact.location
                                AND calls.response_artifact_bucket
                                    <=> artifact.bucket
                                AND calls.response_artifact_key
                                    <=> artifact.object_key
                                AND calls.response_artifact_provider_version_id
                                    <=> artifact.provider_version_id
                                AND calls.response_artifact_etag <=> artifact.etag
                                AND calls.response_artifact_sha256
                                    <=> artifact.expected_sha256
                                AND calls.response_artifact_byte_size
                                    <=> artifact.expected_byte_size
                                AND calls.retention_class
                                    <=> artifact.retention_class
                                AND calls.retention_deadline
                                    <=> artifact.retention_deadline
                        )
                    )
                """
            )
        ).scalar_one()
    )


def upgrade() -> None:
    connection = op.get_bind()
    unmatched_analysis_count = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM product_brief_analysis_requests AS analysis
            WHERE (
                SELECT COUNT(*)
                FROM outbox_events AS event
                WHERE event.workspace_id = analysis.workspace_id
                    AND event.aggregate_id = analysis.product_brief_id
                    AND event.event_type = 'product-brief.requested'
                    AND JSON_UNQUOTE(
                        JSON_EXTRACT(event.payload_json, '$.operation_id')
                    ) = analysis.operation_id
            ) <> 1
            """
        )
    ).scalar_one()
    if unmatched_analysis_count:
        raise RuntimeError(
            "ProductBrief trace migration found analysis rows without exactly one request event"
        )

    trace_column_exists = bool(
        connection.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                    AND table_name = 'product_brief_analysis_requests'
                    AND column_name = 'trace_id'
                """
            )
        ).scalar_one()
    )
    if not trace_column_exists:
        op.add_column(
            "product_brief_analysis_requests",
            sa.Column("trace_id", sa.String(length=64), nullable=True),
        )

    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {_IMMUTABILITY_TRIGGER}"))
    try:
        connection.execute(
            sa.text(
                """
                UPDATE product_brief_analysis_requests AS analysis
                INNER JOIN outbox_events AS event
                    ON event.workspace_id = analysis.workspace_id
                    AND event.aggregate_id = analysis.product_brief_id
                    AND event.event_type = 'product-brief.requested'
                    AND JSON_UNQUOTE(
                        JSON_EXTRACT(event.payload_json, '$.operation_id')
                    ) = analysis.operation_id
                SET analysis.trace_id = event.trace_id
                WHERE analysis.trace_id IS NULL
                """
            )
        )
        missing_trace_count = connection.execute(
            sa.text("SELECT COUNT(*) FROM product_brief_analysis_requests WHERE trace_id IS NULL")
        ).scalar_one()
        if missing_trace_count:
            raise RuntimeError("ProductBrief trace migration could not backfill every analysis row")
        op.alter_column(
            "product_brief_analysis_requests",
            "trace_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
    finally:
        op.execute(
            sa.text(
                f"CREATE TRIGGER {_IMMUTABILITY_TRIGGER} "
                "BEFORE UPDATE ON product_brief_analysis_requests FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                "'product_brief_analysis_requests are immutable'"
            )
        )


def downgrade() -> None:
    connection = op.get_bind()
    trace_lineage_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM product_brief_analysis_requests")
    ).scalar_one()
    if trace_lineage_count:
        raise RuntimeError("cannot downgrade while ProductBrief trace lineage history exists")
    if _unrepresentable_provider_artifact_count(connection):
        raise RuntimeError(
            "provider artifact ledger cannot be represented by the legacy call schema"
        )
    op.drop_column("product_brief_analysis_requests", "trace_id")
