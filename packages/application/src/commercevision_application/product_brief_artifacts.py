"""Durable intent-first ledger for raw ProductBrief provider artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from commercevision_contracts.product_briefs import (
    PreparedProviderArtifact,
    ProviderArtifactReference,
    ProviderArtifactState,
    ProviderArtifactStore,
    ProviderArtifactWrite,
    ProviderArtifactWriteOutcomeUnknownError,
    ProviderArtifactWriteSafeToRetryError,
)
from commercevision_domain import (
    RetentionClass,
    StoragePreconditionError,
    StorageUnavailableError,
    UploadObjectMissingError,
    new_uuid7,
)

from .product_brief_ports import (
    ProductBriefUnitOfWorkFactory,
    ProductBriefUnitOfWorkPort,
    ProviderArtifactPhysicalTargetReader,
    StoredProviderArtifact,
)

ProviderArtifactIntentAuthority = Callable[[ProductBriefUnitOfWorkPort], datetime]


@dataclass(frozen=True, slots=True)
class ProviderArtifactOwner:
    workspace_id: str
    product_brief_id: str


@dataclass(frozen=True, slots=True)
class ProviderArtifactReconciliationCursor:
    updated_at: datetime
    artifact_id: str


@dataclass(frozen=True, slots=True)
class ProviderArtifactReconciliationBatch:
    examined: int
    stored: int
    unknown: int
    intended: int
    next_cursor: ProviderArtifactReconciliationCursor | None


class ProductBriefProviderArtifactService:
    """Commit a frozen target before bytes leave the process."""

    def __init__(
        self,
        *,
        uow_factory: ProductBriefUnitOfWorkFactory,
        artifact_store: ProviderArtifactStore,
        clock: Callable[[], datetime],
    ) -> None:
        self._uow_factory = uow_factory
        self._artifact_store = artifact_store
        self._clock = clock

    def store_artifact(
        self,
        artifact: ProviderArtifactWrite,
        *,
        owner: ProviderArtifactOwner,
        authorize_intent: ProviderArtifactIntentAuthority,
    ) -> ProviderArtifactReference:
        existing = self._find_existing(artifact=artifact, owner=owner)
        if existing is not None:
            target = _prepared_target(existing)
            self._assert_exact_replay(
                existing=existing,
                artifact=artifact,
                owner=owner,
                target=target,
            )
            if existing.state == ProviderArtifactState.STORED:
                return _stored_reference(existing)
            if existing.state == ProviderArtifactState.UNKNOWN:
                raise ProviderArtifactWriteOutcomeUnknownError(
                    "provider artifact write outcome is unknown and requires reconciliation"
                )

        intended, target = self._persist_intent(
            artifact=artifact,
            owner=owner,
            authorize_intent=authorize_intent,
        )
        if intended.state == ProviderArtifactState.STORED:
            return _stored_reference(intended)
        if intended.state == ProviderArtifactState.UNKNOWN:
            raise ProviderArtifactWriteOutcomeUnknownError(
                "provider artifact write outcome is unknown and requires reconciliation"
            )
        try:
            reference = self._artifact_store.write_prepared(artifact, target)
        except ProviderArtifactWriteSafeToRetryError:
            raise
        except ProviderArtifactWriteOutcomeUnknownError:
            self._mark_unknown(
                intended,
                reason="WRITE_OUTCOME_UNKNOWN",
            )
            raise
        except StorageUnavailableError as exc:
            self._mark_unknown(
                intended,
                reason="WRITE_OUTCOME_UNKNOWN",
            )
            raise ProviderArtifactWriteOutcomeUnknownError(
                "provider artifact write outcome could not be proven"
            ) from exc
        except Exception:
            self._mark_unknown(
                intended,
                reason="WRITE_OUTCOME_UNKNOWN",
            )
            raise
        return self._mark_stored(intended, reference)

    def _find_existing(
        self,
        *,
        artifact: ProviderArtifactWrite,
        owner: ProviderArtifactOwner,
    ) -> StoredProviderArtifact | None:
        with self._uow_factory() as uow:
            return uow.product_brief_artifacts.get_provider_artifact(
                workspace_id=owner.workspace_id,
                operation_id=artifact.operation_id,
                operation_attempt=artifact.operation_attempt,
                call_index=artifact.call_index,
                kind=artifact.kind,
            )

    def _persist_intent(
        self,
        *,
        artifact: ProviderArtifactWrite,
        owner: ProviderArtifactOwner,
        authorize_intent: ProviderArtifactIntentAuthority,
    ) -> tuple[StoredProviderArtifact, PreparedProviderArtifact]:
        with self._uow_factory() as uow:
            authorized_at = authorize_intent(uow)
            existing = uow.product_brief_artifacts.get_provider_artifact(
                workspace_id=owner.workspace_id,
                operation_id=artifact.operation_id,
                operation_attempt=artifact.operation_attempt,
                call_index=artifact.call_index,
                kind=artifact.kind,
                for_update=True,
            )
            if existing is not None:
                target = _prepared_target(existing)
                self._assert_exact_replay(
                    existing=existing,
                    artifact=artifact,
                    owner=owner,
                    target=target,
                )
                return existing, target

            artifact_id = new_uuid7()
            write_fence = _write_fence(artifact_id=artifact_id, artifact=artifact)
            target = self._artifact_store.prepare(
                artifact,
                ledger_id=artifact_id,
                write_fence=write_fence,
            )
            intended = StoredProviderArtifact(
                id=artifact_id,
                workspace_id=owner.workspace_id,
                product_brief_id=owner.product_brief_id,
                operation_id=artifact.operation_id,
                operation_attempt=artifact.operation_attempt,
                call_index=artifact.call_index,
                kind=artifact.kind,
                state=ProviderArtifactState.INTENDED,
                key_schema_version=target.key_schema_version,
                storage_backend=target.storage_backend,
                location=target.location,
                bucket=target.bucket,
                key=target.key,
                target_sha256=target.target_sha256,
                content_type=target.content_type,
                expected_sha256=target.expected_sha256,
                expected_byte_size=target.expected_byte_size,
                retention_class=target.retention_class,
                retention_deadline=target.retention_deadline,
                write_fence=target.write_fence,
                provider_version_id=None,
                etag=None,
                unknown_reason=None,
                version=1,
                stored_at=None,
                created_at=authorized_at,
                updated_at=authorized_at,
            )
            uow.product_brief_artifacts.add_provider_artifact(intended)
            if intended.retention_class == RetentionClass.TASK:
                assert intended.retention_deadline is not None
                uow.commit_before_retention_deadline(
                    workspace_id=owner.workspace_id,
                    product_brief_id=owner.product_brief_id,
                    retention_deadline=intended.retention_deadline,
                    clock=self._clock,
                )
            else:
                uow.commit()
            return intended, target

    @staticmethod
    def _assert_exact_replay(
        *,
        existing: StoredProviderArtifact,
        artifact: ProviderArtifactWrite,
        owner: ProviderArtifactOwner,
        target: PreparedProviderArtifact,
    ) -> None:
        expected = (
            owner.workspace_id,
            owner.product_brief_id,
            artifact.operation_id,
            artifact.operation_attempt,
            artifact.call_index,
            artifact.kind,
            target.key_schema_version,
            target.storage_backend,
            target.location,
            target.bucket,
            target.key,
            target.target_sha256,
            artifact.content_type,
            artifact.sha256,
            len(artifact.payload),
            artifact.retention_class,
            artifact.retention_deadline,
            target.write_fence,
        )
        actual = _intent_signature(existing)
        if actual != expected:
            raise StoragePreconditionError("provider artifact intent changed on replay")

    def _mark_stored(
        self,
        intended: StoredProviderArtifact,
        reference: ProviderArtifactReference,
    ) -> ProviderArtifactReference:
        if _reference_signature(reference) != _expected_reference_signature(intended):
            self._mark_unknown(intended, reason="WRITE_REFERENCE_MISMATCH")
            raise StoragePreconditionError(
                "provider artifact write returned a reference outside its frozen target"
            )
        with self._uow_factory() as uow:
            current = uow.product_brief_artifacts.get_provider_artifact_by_id(
                workspace_id=intended.workspace_id,
                artifact_id=intended.id,
                for_update=True,
            )
            if current is None:
                raise StoragePreconditionError("provider artifact intent disappeared")
            if current.state == ProviderArtifactState.STORED:
                persisted = _stored_reference(current)
                if persisted != reference:
                    raise StoragePreconditionError("provider artifact completion changed on replay")
                return persisted
            if current.state != ProviderArtifactState.INTENDED:
                raise StoragePreconditionError(
                    "provider artifact completion raced with an unknown outcome"
                )
            now = uow.database_now()
            stored = replace(
                current,
                state=ProviderArtifactState.STORED,
                provider_version_id=reference.provider_version_id,
                etag=reference.etag,
                unknown_reason=None,
                version=current.version + 1,
                stored_at=now,
                updated_at=now,
            )
            uow.product_brief_artifacts.save_provider_artifact(
                stored,
                workspace_id=intended.workspace_id,
                expected_version=current.version,
            )
            uow.commit()
            return reference

    def _mark_unknown(
        self,
        intended: StoredProviderArtifact,
        *,
        reason: str,
    ) -> None:
        with self._uow_factory() as uow:
            current = uow.product_brief_artifacts.get_provider_artifact_by_id(
                workspace_id=intended.workspace_id,
                artifact_id=intended.id,
                for_update=True,
            )
            if current is None or current.state == ProviderArtifactState.STORED:
                return
            now = uow.database_now()
            unknown = replace(
                current,
                state=ProviderArtifactState.UNKNOWN,
                provider_version_id=None,
                etag=None,
                unknown_reason=reason,
                version=current.version + 1,
                stored_at=None,
                updated_at=now,
            )
            uow.product_brief_artifacts.save_provider_artifact(
                unknown,
                workspace_id=intended.workspace_id,
                expected_version=current.version,
            )
            uow.commit()


class ProductBriefProviderArtifactReconciler:
    """Inspect only one persisted exact key at a time, within explicit bounds."""

    def __init__(
        self,
        *,
        uow_factory: ProductBriefUnitOfWorkFactory,
        artifact_reader: ProviderArtifactPhysicalTargetReader,
        artifact_store: ProviderArtifactStore,
        clock: Callable[[], datetime],
    ) -> None:
        self._uow_factory = uow_factory
        self._artifact_reader = artifact_reader
        self._artifact_store = artifact_store
        self._clock = clock

    def reconcile_artifact(
        self,
        artifact_id: str,
        *,
        workspace_id: str,
        page_size: int,
        max_pages: int,
    ) -> StoredProviderArtifact:
        if max_pages < 1:
            raise ValueError("provider artifact reconciliation requires at least one page")
        with self._uow_factory() as uow:
            artifact = uow.product_brief_artifacts.get_provider_artifact_by_id(
                workspace_id=workspace_id,
                artifact_id=artifact_id,
            )
        if artifact is None:
            raise StoragePreconditionError("provider artifact intent was not found")
        if artifact.state == ProviderArtifactState.STORED:
            return artifact

        target = _prepared_target(artifact)
        entries = []
        continuation_token: str | None = None
        seen_tokens: set[str] = set()
        exhausted_pages = False
        for _ in range(max_pages):
            page = self._artifact_reader.list_versions(
                target,
                page_size=page_size,
                continuation_token=continuation_token,
            )
            entries.extend(page.entries)
            continuation_token = page.continuation_token
            if continuation_token is None:
                break
            if continuation_token in seen_tokens:
                exhausted_pages = True
                break
            seen_tokens.add(continuation_token)
        else:
            exhausted_pages = continuation_token is not None

        if exhausted_pages:
            return self._transition_unknown(
                artifact,
                reason="VERSION_PAGE_LIMIT_EXCEEDED",
            )
        if not entries:
            if artifact.state == ProviderArtifactState.INTENDED:
                return artifact
            return self._transition_unknown(artifact, reason="NO_OBJECT")
        if len(entries) != 1:
            return self._transition_unknown(
                artifact,
                reason="MULTIPLE_VERSIONS",
            )
        entry = entries[0]
        if entry.kind == "DELETE_MARKER":
            return self._transition_unknown(artifact, reason="DELETE_MARKER")
        try:
            stat = self._artifact_reader.stat(target, entry.reference)
        except UploadObjectMissingError:
            return self._transition_unknown(artifact, reason="OBJECT_MISSING")
        if not self._artifact_store.stat_matches(target, stat):
            return self._transition_unknown(artifact, reason="OBJECT_MISMATCH")
        assert stat.reference.version_id is not None
        reference = ProviderArtifactReference(
            storage_backend=stat.backend.value,
            location=stat.reference.location,
            bucket=stat.bucket,
            key=stat.reference.key,
            provider_version_id=stat.reference.version_id,
            etag=stat.etag,
            sha256=artifact.expected_sha256,
            byte_size=stat.content_length,
            retention_class=artifact.retention_class,
            retention_deadline=artifact.retention_deadline,
        )
        return self._transition_stored(artifact, reference=reference)

    def reconcile_batch(
        self,
        *,
        stale_before: datetime,
        limit: int,
        page_size: int,
        max_pages: int,
        after: ProviderArtifactReconciliationCursor | None = None,
    ) -> ProviderArtifactReconciliationBatch:
        if not 1 <= limit <= 100:
            raise ValueError("provider artifact reconciliation batch must contain 1-100 rows")
        with self._uow_factory() as uow:
            candidates = uow.product_brief_artifacts.list_provider_artifacts_for_reconciliation(
                stale_before=stale_before,
                limit=limit,
                after_updated_at=(after.updated_at if after is not None else None),
                after_id=(after.artifact_id if after is not None else None),
            )
        results = tuple(
            self.reconcile_artifact(
                candidate.id,
                workspace_id=candidate.workspace_id,
                page_size=page_size,
                max_pages=max_pages,
            )
            for candidate in candidates
        )
        next_cursor = (
            ProviderArtifactReconciliationCursor(
                updated_at=candidates[-1].updated_at,
                artifact_id=candidates[-1].id,
            )
            if len(candidates) == limit
            else None
        )
        return ProviderArtifactReconciliationBatch(
            examined=len(results),
            stored=sum(row.state == ProviderArtifactState.STORED for row in results),
            unknown=sum(row.state == ProviderArtifactState.UNKNOWN for row in results),
            intended=sum(row.state == ProviderArtifactState.INTENDED for row in results),
            next_cursor=next_cursor,
        )

    def reconcile_operation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
        page_size: int = 100,
        max_pages: int = 4,
    ) -> tuple[StoredProviderArtifact, ...]:
        with self._uow_factory() as uow:
            artifacts = uow.product_brief_artifacts.list_provider_artifacts(
                workspace_id=workspace_id,
                operation_id=operation_id,
                operation_attempt=operation_attempt,
            )
        return tuple(
            self.reconcile_artifact(
                artifact.id,
                workspace_id=artifact.workspace_id,
                page_size=page_size,
                max_pages=max_pages,
            )
            if artifact.state != ProviderArtifactState.STORED
            else artifact
            for artifact in artifacts
        )

    def _transition_stored(
        self,
        snapshot: StoredProviderArtifact,
        *,
        reference: ProviderArtifactReference,
    ) -> StoredProviderArtifact:
        with self._uow_factory() as uow:
            current = uow.product_brief_artifacts.get_provider_artifact_by_id(
                workspace_id=snapshot.workspace_id,
                artifact_id=snapshot.id,
                for_update=True,
            )
            if current is None:
                raise StoragePreconditionError("provider artifact intent disappeared")
            if current.state == ProviderArtifactState.STORED:
                if _stored_reference(current) != reference:
                    raise StoragePreconditionError(
                        "provider artifact reconciliation changed stored evidence"
                    )
                return current
            if _intent_signature(current) != _intent_signature(snapshot):
                raise StoragePreconditionError(
                    "provider artifact intent changed during reconciliation"
                )
            now = uow.database_now()
            stored = replace(
                current,
                state=ProviderArtifactState.STORED,
                provider_version_id=reference.provider_version_id,
                etag=reference.etag,
                unknown_reason=None,
                version=current.version + 1,
                stored_at=now,
                updated_at=now,
            )
            uow.product_brief_artifacts.save_provider_artifact(
                stored,
                workspace_id=snapshot.workspace_id,
                expected_version=current.version,
            )
            uow.commit()
            return stored

    def _transition_unknown(
        self,
        snapshot: StoredProviderArtifact,
        *,
        reason: str,
    ) -> StoredProviderArtifact:
        with self._uow_factory() as uow:
            current = uow.product_brief_artifacts.get_provider_artifact_by_id(
                workspace_id=snapshot.workspace_id,
                artifact_id=snapshot.id,
                for_update=True,
            )
            if current is None:
                raise StoragePreconditionError("provider artifact intent disappeared")
            if current.state == ProviderArtifactState.STORED:
                return current
            if _intent_signature(current) != _intent_signature(snapshot):
                raise StoragePreconditionError(
                    "provider artifact intent changed during reconciliation"
                )
            if current.state == ProviderArtifactState.UNKNOWN and current.unknown_reason == reason:
                return current
            now = uow.database_now()
            unknown = replace(
                current,
                state=ProviderArtifactState.UNKNOWN,
                provider_version_id=None,
                etag=None,
                unknown_reason=reason,
                version=current.version + 1,
                stored_at=None,
                updated_at=now,
            )
            uow.product_brief_artifacts.save_provider_artifact(
                unknown,
                workspace_id=snapshot.workspace_id,
                expected_version=current.version,
            )
            uow.commit()
            return unknown


def _write_fence(
    *,
    artifact_id: str,
    artifact: ProviderArtifactWrite,
) -> str:
    value = "\0".join(
        (
            artifact_id,
            artifact.operation_id,
            str(artifact.operation_attempt),
            str(artifact.call_index),
            artifact.kind.value,
            artifact.sha256,
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prepared_target(artifact: StoredProviderArtifact) -> PreparedProviderArtifact:
    return PreparedProviderArtifact(
        ledger_id=artifact.id,
        key_schema_version=artifact.key_schema_version,
        storage_backend=artifact.storage_backend,
        location=artifact.location,
        bucket=artifact.bucket,
        key=artifact.key,
        target_sha256=artifact.target_sha256,
        content_type=artifact.content_type,
        expected_sha256=artifact.expected_sha256,
        expected_byte_size=artifact.expected_byte_size,
        retention_class=artifact.retention_class,
        retention_deadline=artifact.retention_deadline,
        write_fence=artifact.write_fence,
    )


def _intent_signature(artifact: StoredProviderArtifact) -> tuple[object, ...]:
    return (
        artifact.workspace_id,
        artifact.product_brief_id,
        artifact.operation_id,
        artifact.operation_attempt,
        artifact.call_index,
        artifact.kind,
        artifact.key_schema_version,
        artifact.storage_backend,
        artifact.location,
        artifact.bucket,
        artifact.key,
        artifact.target_sha256,
        artifact.content_type,
        artifact.expected_sha256,
        artifact.expected_byte_size,
        artifact.retention_class,
        artifact.retention_deadline,
        artifact.write_fence,
    )


def _stored_reference(artifact: StoredProviderArtifact) -> ProviderArtifactReference:
    if (
        artifact.state != ProviderArtifactState.STORED
        or artifact.provider_version_id is None
        or artifact.etag is None
    ):
        raise StoragePreconditionError("provider artifact has no exact stored reference")
    return ProviderArtifactReference(
        storage_backend=artifact.storage_backend,
        location=artifact.location,
        bucket=artifact.bucket,
        key=artifact.key,
        provider_version_id=artifact.provider_version_id,
        etag=artifact.etag,
        sha256=artifact.expected_sha256,
        byte_size=artifact.expected_byte_size,
        retention_class=artifact.retention_class,
        retention_deadline=artifact.retention_deadline,
    )


def _reference_signature(
    reference: ProviderArtifactReference,
) -> tuple[object, ...]:
    return (
        reference.storage_backend,
        reference.location,
        reference.bucket,
        reference.key,
        reference.sha256,
        reference.byte_size,
        reference.retention_class,
        reference.retention_deadline,
    )


def _expected_reference_signature(
    artifact: StoredProviderArtifact,
) -> tuple[object, ...]:
    return (
        artifact.storage_backend,
        artifact.location,
        artifact.bucket,
        artifact.key,
        artifact.expected_sha256,
        artifact.expected_byte_size,
        artifact.retention_class,
        artifact.retention_deadline,
    )
