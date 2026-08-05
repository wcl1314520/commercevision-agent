from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from commercevision_domain import (
    DataIntegrityError,
    PlanningContextPolicy,
    PlanningContextSnapshot,
    PlanningContextSource,
    PlanningContextSourceKind,
    build_planning_context,
)
from commercevision_persistence.planning_context_models import PlanningContextSnapshotModel
from commercevision_persistence.planning_contexts import PlanningContextSnapshotRepository
from sqlalchemy import Table, create_engine, event, func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 5, 2, 0, tzinfo=UTC)
WORKSPACE_ID = "planning-domain"
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000501"


def _snapshot() -> PlanningContextSnapshot:
    product_brief = PlanningContextSource.create(
        kind=PlanningContextSourceKind.PRODUCT_BRIEF,
        source_id="019b0000-0000-7000-8000-000000000502",
        version_number=3,
        content_sha256="1" * 64,
        content={"title": "Travel mug"},
    )
    citation = PlanningContextSource.create(
        kind=PlanningContextSourceKind.RETRIEVAL_CITATION,
        source_id="019b0000-0000-7000-8000-000000000503",
        version_number=None,
        content_sha256="2" * 64,
        content={"caption": "Warm kitchen"},
        authority_id="019b0000-0000-7000-8000-000000000504",
        authority_version=4,
        retrieval_run_id="019b0000-0000-7000-8000-000000000507",
        retrieval_policy_version="retrieval-v1",
        retrieval_rank=1,
        citation_id="retrieval-1",
        image_count=1,
    )
    omitted = PlanningContextSource.create(
        kind=PlanningContextSourceKind.RETRIEVAL_CITATION,
        source_id="019b0000-0000-7000-8000-000000000505",
        version_number=None,
        content_sha256="3" * 64,
        content={"caption": "Retained provenance despite clipping"},
        authority_id="019b0000-0000-7000-8000-000000000506",
        authority_version=2,
        retrieval_run_id="019b0000-0000-7000-8000-000000000507",
        retrieval_policy_version="retrieval-v1",
        retrieval_rank=2,
        citation_id="retrieval-2",
        image_count=1,
    )
    return build_planning_context(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        product_brief=product_brief,
        brand_profile=None,
        retrieval_citations=(citation, omitted),
        policy=PlanningContextPolicy(
            version="planning-context-v1",
            maximum_tokens=2_000,
            maximum_images=1,
        ),
    )


def _engine() -> Engine:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def register_mysql_binary_collation(dbapi_connection: Any, _connection_record: object) -> None:
        dbapi_connection.create_collation(
            "utf8mb4_0900_bin",
            lambda left, right: (left > right) - (left < right),
        )
        dbapi_connection.create_function(
            "regexp",
            2,
            lambda pattern, value: re.search(pattern, value) is not None,
        )

    cast(Table, PlanningContextSnapshotModel.__table__).create(engine)
    return engine


def test_repository_round_trips_snapshot_and_hides_other_workspaces() -> None:
    engine = _engine()
    snapshot = _snapshot()
    with Session(engine, expire_on_commit=False) as session:
        repository = PlanningContextSnapshotRepository(session, clock=lambda: NOW)
        repository.save(snapshot, retain_until=NOW + timedelta(days=30))
        repository.save(snapshot, retain_until=NOW + timedelta(days=30))
        session.commit()

        assert session.scalar(select(func.count()).select_from(PlanningContextSnapshotModel)) == 1
        restored = repository.get(
            workspace_id=WORKSPACE_ID,
            workflow_id=WORKFLOW_ID,
            context_sha256=snapshot.context_sha256,
        )
        assert restored == snapshot
        assert restored is not None
        assert restored.context_sha256 == snapshot.context_sha256
        assert (
            repository.get(
                workspace_id="another-workspace",
                workflow_id=WORKFLOW_ID,
                context_sha256=snapshot.context_sha256,
            )
            is None
        )


def test_repository_fails_closed_when_stored_snapshot_is_tampered() -> None:
    engine = _engine()
    snapshot = _snapshot()
    with Session(engine, expire_on_commit=False) as session:
        repository = PlanningContextSnapshotRepository(session, clock=lambda: NOW)
        repository.save(snapshot, retain_until=NOW + timedelta(days=30))
        session.commit()
        model = session.scalar(
            select(PlanningContextSnapshotModel).where(
                PlanningContextSnapshotModel.workspace_id == WORKSPACE_ID
            )
        )
        assert model is not None
        tampered = deepcopy(model.snapshot_json)
        tampered["omitted"][0]["source"]["content"] = {"caption": "tampered but omitted"}
        session.execute(
            update(PlanningContextSnapshotModel)
            .where(PlanningContextSnapshotModel.workspace_id == WORKSPACE_ID)
            .values(snapshot_json=tampered)
        )
        session.commit()

        with pytest.raises(DataIntegrityError, match="cannot be reconstructed"):
            repository.get(
                workspace_id=WORKSPACE_ID,
                workflow_id=WORKFLOW_ID,
                context_sha256=snapshot.context_sha256,
            )
