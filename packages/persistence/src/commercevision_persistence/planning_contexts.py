"""Tenant-scoped immutable Planning Context snapshot persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from commercevision_domain import (
    DataIntegrityError,
    PlanningContextIncludedSource,
    PlanningContextOmissionReason,
    PlanningContextOmittedSource,
    PlanningContextPolicy,
    PlanningContextSnapshot,
    PlanningContextSource,
)
from sqlalchemy import insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .planning_context_models import PlanningContextSnapshotModel


def _source_to_storage(source: PlanningContextSource) -> dict[str, object]:
    return {
        **source.identity_data(),
        "content": source.content(),
        "image_count": source.image_count,
    }


def _source_from_storage(value: object) -> PlanningContextSource:
    if not isinstance(value, dict):
        raise ValueError("stored Planning Context source is invalid")
    data = cast(dict[str, Any], value)
    content = data.get("content")
    if not isinstance(content, dict):
        raise ValueError("stored Planning Context source content is invalid")
    return PlanningContextSource.create(
        kind=data["kind"],
        source_id=data["source_id"],
        version_number=data.get("version_number"),
        content_sha256=data["content_sha256"],
        content=cast(dict[str, object], content),
        authority_id=data.get("authority_id"),
        authority_version=data.get("authority_version"),
        retrieval_run_id=data.get("retrieval_run_id"),
        retrieval_policy_version=data.get("retrieval_policy_version"),
        retrieval_rank=data.get("retrieval_rank"),
        citation_id=data.get("citation_id"),
        image_count=data["image_count"],
    )


def _snapshot_to_storage(snapshot: PlanningContextSnapshot) -> dict[str, object]:
    return {
        "canonical": snapshot.to_canonical_data(),
        "included": [
            {
                "source": _source_to_storage(item.source),
                "citation_number": item.citation_number,
            }
            for item in snapshot.included_sources
        ],
        "omitted": [
            {"source": _source_to_storage(item.source), "reason": item.reason.value}
            for item in snapshot.omitted_sources
        ],
    }


def _storage_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _snapshot_from_storage(value: object) -> PlanningContextSnapshot:
    if not isinstance(value, dict):
        raise ValueError("stored Planning Context snapshot is invalid")
    storage = cast(dict[str, Any], value)
    canonical = storage.get("canonical")
    included_data = storage.get("included")
    omitted_data = storage.get("omitted")
    if (
        not isinstance(canonical, dict)
        or not isinstance(included_data, list)
        or not isinstance(omitted_data, list)
    ):
        raise ValueError("stored Planning Context snapshot is incomplete")
    canonical_data = cast(dict[str, Any], canonical)
    policy_data = canonical_data.get("policy")
    budget_usage = canonical_data.get("budget_usage")
    if not isinstance(policy_data, dict) or not isinstance(budget_usage, dict):
        raise ValueError("stored Planning Context policy is invalid")
    policy_values = cast(dict[str, Any], policy_data)
    budget_values = cast(dict[str, Any], budget_usage)
    included: list[PlanningContextIncludedSource] = []
    for item in included_data:
        if not isinstance(item, dict):
            raise ValueError("stored included Planning Context source is invalid")
        item_data = cast(dict[str, Any], item)
        included.append(
            PlanningContextIncludedSource(
                source=_source_from_storage(item_data.get("source")),
                citation_number=item_data.get("citation_number"),
            )
        )
    omitted: list[PlanningContextOmittedSource] = []
    for item in omitted_data:
        if not isinstance(item, dict):
            raise ValueError("stored omitted Planning Context source is invalid")
        item_data = cast(dict[str, Any], item)
        omitted.append(
            PlanningContextOmittedSource(
                source=_source_from_storage(item_data.get("source")),
                reason=PlanningContextOmissionReason(str(item_data["reason"])),
            )
        )
    snapshot = PlanningContextSnapshot(
        workspace_id=canonical_data["workspace_id"],
        workflow_id=canonical_data["workflow_id"],
        policy=PlanningContextPolicy(
            version=policy_values["version"],
            maximum_tokens=policy_values["maximum_tokens"],
            maximum_images=policy_values["maximum_images"],
        ),
        included_sources=tuple(included),
        omitted_sources=tuple(omitted),
        used_tokens=budget_values["tokens"],
        used_images=budget_values["images"],
        schema_version=canonical_data["schema_version"],
    )
    if snapshot.to_canonical_data() != canonical_data:
        raise ValueError("stored Planning Context canonical data is inconsistent")
    return snapshot


class PlanningContextSnapshotRepository:
    def __init__(self, session: Session, *, clock: Callable[[], datetime] | None = None) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    def save(self, snapshot: PlanningContextSnapshot, *, retain_until: datetime) -> None:
        if retain_until.tzinfo is None or retain_until.utcoffset() is None:
            raise ValueError("Planning Context retention deadline must be timezone-aware")
        snapshot_json = _snapshot_to_storage(snapshot)
        values = {
            "workspace_id": snapshot.workspace_id,
            "workflow_id": snapshot.workflow_id,
            "context_sha256": snapshot.context_sha256,
            "schema_version": snapshot.schema_version,
            "policy_version": snapshot.policy.version,
            "snapshot_json": snapshot_json,
            "storage_sha256": _storage_sha256(snapshot_json),
            "source_count": len(snapshot.included_sources) + len(snapshot.omitted_sources),
            "retain_until": retain_until.astimezone(UTC),
            "created_at": self._clock().astimezone(UTC),
        }
        bind = self._session.get_bind()
        if bind.dialect.name == "mysql":
            if (
                self._get_model(
                    workspace_id=snapshot.workspace_id,
                    workflow_id=snapshot.workflow_id,
                    context_sha256=snapshot.context_sha256,
                )
                is None
            ):
                try:
                    with self._session.begin_nested():
                        self._session.execute(insert(PlanningContextSnapshotModel).values(**values))
                except IntegrityError:
                    # A concurrent writer may have committed the same immutable fact.
                    self._session.expire_all()
        elif bind.dialect.name == "sqlite":
            sqlite_statement = (
                sqlite_insert(PlanningContextSnapshotModel)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=("workspace_id", "workflow_id", "context_sha256")
                )
            )
            self._session.execute(sqlite_statement)
        else:
            raise RuntimeError("Planning Context persistence requires MySQL or SQLite tests")
        model = self._get_model(
            workspace_id=snapshot.workspace_id,
            workflow_id=snapshot.workflow_id,
            context_sha256=snapshot.context_sha256,
        )
        if model is None or (
            model.snapshot_json != values["snapshot_json"]
            or model.storage_sha256 != values["storage_sha256"]
            or model.retain_until != retain_until.astimezone(UTC)
        ):
            raise DataIntegrityError("Planning Context hash already stores different facts")

    def get(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        context_sha256: str,
    ) -> PlanningContextSnapshot | None:
        model = self._get_model(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            context_sha256=context_sha256,
        )
        if model is None:
            return None
        if _storage_sha256(model.snapshot_json) != model.storage_sha256:
            raise DataIntegrityError("Planning Context cannot be reconstructed")
        try:
            snapshot = _snapshot_from_storage(model.snapshot_json)
        except (KeyError, TypeError, ValueError) as exc:
            raise DataIntegrityError("Planning Context cannot be reconstructed") from exc
        if (
            snapshot.workspace_id != model.workspace_id
            or snapshot.workflow_id != model.workflow_id
            or snapshot.context_sha256 != model.context_sha256
            or snapshot.schema_version != model.schema_version
            or snapshot.policy.version != model.policy_version
            or len(snapshot.included_sources) + len(snapshot.omitted_sources) != model.source_count
        ):
            raise DataIntegrityError("Planning Context cannot be reconstructed")
        return snapshot

    def _get_model(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        context_sha256: str,
    ) -> PlanningContextSnapshotModel | None:
        return self._session.scalar(
            select(PlanningContextSnapshotModel).where(
                PlanningContextSnapshotModel.workspace_id == workspace_id,
                PlanningContextSnapshotModel.workflow_id == workflow_id,
                PlanningContextSnapshotModel.context_sha256 == context_sha256,
            )
        )


class SqlAlchemyPlanningContextSnapshotStore:
    """Transaction-owning application adapter for immutable Context snapshots."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, snapshot: PlanningContextSnapshot, *, retain_until: datetime) -> None:
        with self._session_factory.begin() as session:
            PlanningContextSnapshotRepository(session).save(
                snapshot,
                retain_until=retain_until,
            )

    def get(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        context_sha256: str,
    ) -> PlanningContextSnapshot | None:
        with self._session_factory() as session:
            return PlanningContextSnapshotRepository(session).get(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                context_sha256=context_sha256,
            )
