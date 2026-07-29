from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application.product_brief_artifacts import (
    ProductBriefProviderArtifactReconciler,
    ProductBriefProviderArtifactService,
    ProviderArtifactOwner,
)
from commercevision_application.product_brief_ports import StoredProviderArtifact
from commercevision_contracts.object_storage import (
    ConditionalWriteRequest,
    ObjectReference,
    ObjectStat,
    ObjectVersionEntry,
    ObjectVersionListRequest,
    ObjectVersionPage,
    ServerSideEncryptionState,
)
from commercevision_contracts.product_briefs import (
    ProviderArtifactKind,
    ProviderArtifactState,
    ProviderArtifactWrite,
    ProviderArtifactWriteOutcomeUnknownError,
    ProviderArtifactWriteSafeToRetryError,
)
from commercevision_domain import (
    RetentionClass,
    StorageBackend,
    StorageLocationClass,
    StoragePreconditionError,
    StorageUnavailableError,
    UploadObjectMissingError,
)
from commercevision_object_storage import (
    ObjectStorageProviderArtifactSink,
    ObjectStorageProviderArtifactTarget,
    ObjectStorageProviderArtifactTargetRegistry,
)
from commercevision_persistence.product_brief_models import (
    ProductBriefProviderCallModel,
)

NOW = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(hours=1)
WORKSPACE_ID = "ledger-workspace"
PRODUCT_BRIEF_ID = "019fa000-0000-7000-8000-000000000001"
OPERATION_ID = "019fa000-0000-7000-8000-000000000002"


class SimulatedCrash(BaseException):
    pass


class MemoryProviderArtifactRepository:
    def __init__(self, events: list[str]) -> None:
        self.rows: dict[str, StoredProviderArtifact] = {}
        self.events = events

    def add_provider_artifact(self, artifact: StoredProviderArtifact) -> None:
        if self.get_provider_artifact(
            workspace_id=artifact.workspace_id,
            operation_id=artifact.operation_id,
            operation_attempt=artifact.operation_attempt,
            call_index=artifact.call_index,
            kind=artifact.kind,
        ):
            raise AssertionError("duplicate logical artifact")
        self.rows[artifact.id] = artifact
        self.events.append(f"add:{artifact.state.value}")

    def get_provider_artifact(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
        call_index: int,
        kind: ProviderArtifactKind,
        for_update: bool = False,
    ) -> StoredProviderArtifact | None:
        del for_update
        return next(
            (
                artifact
                for artifact in self.rows.values()
                if (
                    artifact.workspace_id,
                    artifact.operation_id,
                    artifact.operation_attempt,
                    artifact.call_index,
                    artifact.kind,
                )
                == (
                    workspace_id,
                    operation_id,
                    operation_attempt,
                    call_index,
                    kind,
                )
            ),
            None,
        )

    def get_provider_artifact_by_id(
        self,
        *,
        workspace_id: str,
        artifact_id: str,
        for_update: bool = False,
    ) -> StoredProviderArtifact | None:
        del for_update
        artifact = self.rows.get(artifact_id)
        if artifact is None or artifact.workspace_id != workspace_id:
            return None
        return artifact

    def save_provider_artifact(
        self,
        artifact: StoredProviderArtifact,
        *,
        workspace_id: str,
        expected_version: int,
    ) -> None:
        current = self.rows.get(artifact.id)
        if (
            current is None
            or current.workspace_id != workspace_id
            or current.version != expected_version
        ):
            raise AssertionError("optimistic artifact version mismatch")
        self.rows[artifact.id] = artifact
        self.events.append(f"save:{artifact.state.value}")

    def list_provider_artifacts(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
    ) -> tuple[StoredProviderArtifact, ...]:
        return tuple(
            sorted(
                (
                    artifact
                    for artifact in self.rows.values()
                    if (
                        artifact.workspace_id == workspace_id
                        and artifact.operation_id == operation_id
                        and artifact.operation_attempt == operation_attempt
                    )
                ),
                key=lambda artifact: (artifact.call_index, artifact.kind.value),
            )
        )

    def list_provider_artifacts_for_reconciliation(
        self,
        *,
        stale_before: datetime,
        limit: int,
        after_updated_at: datetime | None = None,
        after_id: str | None = None,
    ) -> tuple[StoredProviderArtifact, ...]:
        candidates = sorted(
            (
                artifact
                for artifact in self.rows.values()
                if (
                    artifact.state
                    in {ProviderArtifactState.INTENDED, ProviderArtifactState.UNKNOWN}
                    and artifact.updated_at <= stale_before
                    and (
                        after_updated_at is None
                        or (artifact.updated_at, artifact.id) > (after_updated_at, after_id or "")
                    )
                )
            ),
            key=lambda artifact: (artifact.updated_at, artifact.id),
        )
        return tuple(candidates[:limit])


class MemoryUnitOfWork:
    def __init__(
        self,
        repository: MemoryProviderArtifactRepository,
        events: list[str],
    ) -> None:
        self.product_briefs = repository
        self.product_brief_artifacts = repository
        self._events = events

    def __enter__(self) -> MemoryUnitOfWork:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def database_now(self) -> datetime:
        return NOW

    def commit(self) -> None:
        states = ",".join(artifact.state.value for artifact in self.product_briefs.rows.values())
        self._events.append(f"commit:{states}")

    def commit_before_retention_deadline(self, **kwargs) -> None:
        assert kwargs["retention_deadline"] == DEADLINE
        self.commit()


class MemoryUnitOfWorkFactory:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.repository = MemoryProviderArtifactRepository(self.events)

    def __call__(self) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(self.repository, self.events)


class VersionedMemoryStorage:
    def __init__(
        self,
        *,
        backend: StorageBackend = StorageBackend.MINIO,
        bucket: str = "provider-results",
    ) -> None:
        self.backend = backend
        self.bucket = bucket
        self.mode = "success"
        self.list_error: Exception | None = None
        self.stat_error: Exception | None = None
        self.write_count = 0
        self.list_count = 0
        self.entries: list[ObjectVersionEntry] = []
        self.stats: dict[str, ObjectStat] = {}

    def write_if_absent(self, request: ConditionalWriteRequest) -> ObjectStat:
        self.write_count += 1
        if self.mode == "crash_before":
            raise SimulatedCrash
        version_id = f"version-{self.write_count}"
        stat = ObjectStat(
            reference=request.reference.model_copy(update={"version_id": version_id}),
            backend=self.backend,
            bucket=self.bucket,
            etag=f'"etag-{self.write_count}"',
            content_length=len(request.payload),
            content_type=request.content_type,
            checksum_sha256_base64=None,
            metadata={**request.metadata, "sha256": request.expected_sha256},
            last_modified=NOW,
            server_side_encryption=ServerSideEncryptionState.AES256,
        )
        self.entries.append(ObjectVersionEntry(reference=stat.reference, kind="OBJECT"))
        self.stats[version_id] = stat
        if self.mode == "crash_after":
            raise SimulatedCrash
        if self.mode == "timeout_after":
            raise StorageUnavailableError("committed write timed out")
        return stat

    def list_versions(self, request: ObjectVersionListRequest) -> ObjectVersionPage:
        self.list_count += 1
        if self.list_error is not None:
            raise self.list_error
        offset = int(request.continuation_token or "0")
        entries = tuple(self.entries[offset : offset + request.page_size])
        next_offset = offset + len(entries)
        return ObjectVersionPage(
            entries=entries,
            continuation_token=(str(next_offset) if next_offset < len(self.entries) else None),
        )

    def stat(self, reference: ObjectReference) -> ObjectStat:
        if self.stat_error is not None:
            raise self.stat_error
        if reference.version_id is None or reference.version_id not in self.stats:
            raise UploadObjectMissingError("missing exact object")
        return self.stats[reference.version_id]

    def configured_bucket(self, location: StorageLocationClass) -> str:
        assert location == StorageLocationClass.PROVIDER_RESULT
        return self.bucket


def _artifact(
    *,
    call_index: int = 0,
    kind: ProviderArtifactKind = ProviderArtifactKind.REQUEST,
    payload: bytes = b'{"request":"raw"}',
) -> ProviderArtifactWrite:
    return ProviderArtifactWrite(
        operation_id=OPERATION_ID,
        operation_attempt=1,
        call_index=call_index,
        kind=kind,
        content_type="application/json",
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        retention_class=RetentionClass.TASK,
        retention_deadline=DEADLINE,
    )


def _services():
    uow_factory = MemoryUnitOfWorkFactory()
    storage = VersionedMemoryStorage()
    artifact_store = ObjectStorageProviderArtifactSink(
        storage,  # type: ignore[arg-type]
        bucket="provider-results",
    )
    service = ProductBriefProviderArtifactService(
        uow_factory=uow_factory,  # type: ignore[arg-type]
        artifact_store=artifact_store,
        clock=lambda: NOW,
    )
    targets = ObjectStorageProviderArtifactTargetRegistry(
        (
            ObjectStorageProviderArtifactTarget(
                storage=storage,  # type: ignore[arg-type]
                bucket=storage.bucket,
            ),
        )
    )
    reconciler = ProductBriefProviderArtifactReconciler(
        uow_factory=uow_factory,  # type: ignore[arg-type]
        artifact_reader=targets,
        artifact_store=artifact_store,
        clock=lambda: NOW,
    )
    return uow_factory, storage, service, reconciler


def _owner() -> ProviderArtifactOwner:
    return ProviderArtifactOwner(
        workspace_id=WORKSPACE_ID,
        product_brief_id=PRODUCT_BRIEF_ID,
    )


def _authorize(uow) -> datetime:
    uow._events.append("authority")
    return uow.database_now()


def _only_row(factory: MemoryUnitOfWorkFactory) -> StoredProviderArtifact:
    return next(iter(factory.repository.rows.values()))


def test_completed_call_model_requires_request_artifact_ledger_link() -> None:
    column = ProductBriefProviderCallModel.__table__.c.request_artifact_id

    assert column.nullable is False


def test_artifact_intent_and_exact_completion_use_separate_commits() -> None:
    factory, storage, service, _ = _services()

    reference = service.store_artifact(
        _artifact(),
        owner=_owner(),
        authorize_intent=_authorize,
    )

    assert reference.provider_version_id == "version-1"
    assert _only_row(factory).state == ProviderArtifactState.STORED
    assert factory.events == [
        "authority",
        "add:INTENDED",
        "commit:INTENDED",
        "save:STORED",
        "commit:STORED",
    ]
    assert storage.write_count == 1


def test_pre_write_storage_failure_preserves_intent_and_retries_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, storage, service, _ = _services()
    artifact_store = service._artifact_store
    write_prepared = artifact_store.write_prepared
    attempts = 0

    def fail_before_first_write(artifact, prepared):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderArtifactWriteSafeToRetryError(
                "provider artifact storage unavailable before write"
            )
        return write_prepared(artifact, prepared)

    monkeypatch.setattr(artifact_store, "write_prepared", fail_before_first_write)

    with pytest.raises(
        ProviderArtifactWriteSafeToRetryError,
        match="unavailable before write",
    ):
        service.store_artifact(
            _artifact(),
            owner=_owner(),
            authorize_intent=_authorize,
        )

    intended = _only_row(factory)
    assert intended.state == ProviderArtifactState.INTENDED
    assert intended.unknown_reason is None
    assert storage.write_count == 0

    reference = service.store_artifact(
        _artifact(),
        owner=_owner(),
        authorize_intent=_authorize,
    )

    assert reference.provider_version_id == "version-1"
    assert _only_row(factory).state == ProviderArtifactState.STORED
    assert storage.write_count == 1


@pytest.mark.parametrize(
    ("mode", "has_object"),
    [("crash_before", False), ("crash_after", True)],
)
def test_artifact_intent_survives_process_crash(
    mode: str,
    has_object: bool,
) -> None:
    factory, storage, service, reconciler = _services()
    storage.mode = mode

    with pytest.raises(SimulatedCrash):
        service.store_artifact(
            _artifact(),
            owner=_owner(),
            authorize_intent=_authorize,
        )

    intended = _only_row(factory)
    assert intended.state == ProviderArtifactState.INTENDED
    assert intended.key
    assert bool(storage.entries) is has_object

    reconciler.reconcile_artifact(
        intended.id,
        workspace_id=WORKSPACE_ID,
        page_size=2,
        max_pages=2,
    )
    expected_state = ProviderArtifactState.STORED if has_object else ProviderArtifactState.INTENDED
    assert _only_row(factory).state == expected_state


def test_reconciler_rejects_artifact_id_from_another_workspace() -> None:
    factory, storage, service, reconciler = _services()
    storage.mode = "crash_before"
    with pytest.raises(SimulatedCrash):
        service.store_artifact(
            _artifact(),
            owner=_owner(),
            authorize_intent=_authorize,
        )
    artifact = _only_row(factory)

    with pytest.raises(StoragePreconditionError, match="not found"):
        reconciler.reconcile_artifact(
            artifact.id,
            workspace_id="another-workspace",
            page_size=2,
            max_pages=2,
        )

    assert storage.list_count == 0
    assert _only_row(factory) == artifact


def test_committed_then_timeout_is_unknown_and_remains_discoverable() -> None:
    factory, storage, service, reconciler = _services()
    storage.mode = "timeout_after"

    with pytest.raises(StorageUnavailableError, match="timed out"):
        service.store_artifact(
            _artifact(),
            owner=_owner(),
            authorize_intent=_authorize,
        )

    unknown = _only_row(factory)
    assert unknown.state == ProviderArtifactState.UNKNOWN
    assert unknown.unknown_reason == "WRITE_OUTCOME_UNKNOWN"
    assert unknown.key.endswith("/request.json")
    assert unknown.provider_version_id is None

    reconciler.reconcile_artifact(
        unknown.id,
        workspace_id=WORKSPACE_ID,
        page_size=2,
        max_pages=2,
    )
    stored = _only_row(factory)
    assert stored.state == ProviderArtifactState.STORED
    assert stored.provider_version_id == "version-1"


@pytest.mark.parametrize(
    ("legacy_backend", "legacy_bucket", "current_backend", "current_bucket"),
    [
        (
            StorageBackend.MINIO,
            "provider-results-old",
            StorageBackend.MINIO,
            "provider-results-new",
        ),
        (
            StorageBackend.MINIO,
            "provider-results-old",
            StorageBackend.OSS,
            "provider-results-old",
        ),
    ],
)
def test_reconciler_routes_to_the_ledger_physical_target_after_storage_drift(
    legacy_backend: StorageBackend,
    legacy_bucket: str,
    current_backend: StorageBackend,
    current_bucket: str,
) -> None:
    factory = MemoryUnitOfWorkFactory()
    legacy_storage = VersionedMemoryStorage(
        backend=legacy_backend,
        bucket=legacy_bucket,
    )
    artifact_store = ObjectStorageProviderArtifactSink(
        legacy_storage,  # type: ignore[arg-type]
        bucket=legacy_bucket,
    )
    service = ProductBriefProviderArtifactService(
        uow_factory=factory,  # type: ignore[arg-type]
        artifact_store=artifact_store,
        clock=lambda: NOW,
    )
    legacy_storage.mode = "timeout_after"
    with pytest.raises(ProviderArtifactWriteOutcomeUnknownError):
        service.store_artifact(
            _artifact(),
            owner=_owner(),
            authorize_intent=_authorize,
        )
    unknown = _only_row(factory)

    current_storage = VersionedMemoryStorage(
        backend=current_backend,
        bucket=current_bucket,
    )
    targets = ObjectStorageProviderArtifactTargetRegistry(
        (
            ObjectStorageProviderArtifactTarget(
                storage=current_storage,  # type: ignore[arg-type]
                location=StorageLocationClass.PROVIDER_RESULT,
                bucket=current_bucket,
            ),
            ObjectStorageProviderArtifactTarget(
                storage=legacy_storage,  # type: ignore[arg-type]
                location=StorageLocationClass.PROVIDER_RESULT,
                bucket=legacy_bucket,
            ),
        )
    )
    reconciler = ProductBriefProviderArtifactReconciler(
        uow_factory=factory,  # type: ignore[arg-type]
        artifact_reader=targets,
        artifact_store=artifact_store,
        clock=lambda: NOW,
    )

    reconciler.reconcile_artifact(
        unknown.id,
        workspace_id=WORKSPACE_ID,
        page_size=2,
        max_pages=2,
    )

    stored = _only_row(factory)
    assert stored.state == ProviderArtifactState.STORED
    assert stored.storage_backend == legacy_backend.value
    assert stored.bucket == legacy_bucket
    assert stored.provider_version_id == "version-1"
    assert legacy_storage.list_count == 1
    assert current_storage.list_count == 0


def test_reconciler_fails_closed_when_the_ledger_physical_target_is_unregistered() -> None:
    factory = MemoryUnitOfWorkFactory()
    legacy_storage = VersionedMemoryStorage(bucket="provider-results-old")
    artifact_store = ObjectStorageProviderArtifactSink(
        legacy_storage,  # type: ignore[arg-type]
        bucket=legacy_storage.bucket,
    )
    service = ProductBriefProviderArtifactService(
        uow_factory=factory,  # type: ignore[arg-type]
        artifact_store=artifact_store,
        clock=lambda: NOW,
    )
    legacy_storage.mode = "timeout_after"
    with pytest.raises(ProviderArtifactWriteOutcomeUnknownError):
        service.store_artifact(
            _artifact(),
            owner=_owner(),
            authorize_intent=_authorize,
        )
    unknown = _only_row(factory)

    current_storage = VersionedMemoryStorage(bucket="provider-results-new")
    targets = ObjectStorageProviderArtifactTargetRegistry(
        (
            ObjectStorageProviderArtifactTarget(
                storage=current_storage,  # type: ignore[arg-type]
                location=StorageLocationClass.PROVIDER_RESULT,
                bucket=current_storage.bucket,
            ),
        )
    )
    reconciler = ProductBriefProviderArtifactReconciler(
        uow_factory=factory,  # type: ignore[arg-type]
        artifact_reader=targets,
        artifact_store=artifact_store,
        clock=lambda: NOW,
    )

    with pytest.raises(StoragePreconditionError, match="physical target is not registered"):
        reconciler.reconcile_artifact(
            unknown.id,
            workspace_id=WORKSPACE_ID,
            page_size=2,
            max_pages=2,
        )

    assert _only_row(factory) == unknown
    assert current_storage.list_count == 0


def test_physical_target_registry_rejects_a_mismatched_adapter_bucket() -> None:
    storage = VersionedMemoryStorage(bucket="provider-results-current")

    with pytest.raises(ValueError, match="does not match storage adapter"):
        ObjectStorageProviderArtifactTargetRegistry(
            (
                ObjectStorageProviderArtifactTarget(
                    storage=storage,  # type: ignore[arg-type]
                    bucket="provider-results-legacy",
                ),
            )
        )


@pytest.mark.parametrize(
    "kind",
    [ProviderArtifactKind.REQUEST, ProviderArtifactKind.RESPONSE],
)
def test_existing_unknown_replay_remains_outcome_unknown_without_another_write(
    kind: ProviderArtifactKind,
) -> None:
    factory, storage, service, _ = _services()
    storage.mode = "timeout_after"

    with pytest.raises(ProviderArtifactWriteOutcomeUnknownError, match="could not be proven"):
        service.store_artifact(
            _artifact(kind=kind),
            owner=_owner(),
            authorize_intent=_authorize,
        )
    unknown = _only_row(factory)

    with pytest.raises(
        ProviderArtifactWriteOutcomeUnknownError,
        match="requires reconciliation",
    ):
        service.store_artifact(
            _artifact(kind=kind),
            owner=_owner(),
            authorize_intent=_authorize,
        )

    assert _only_row(factory) == unknown
    assert unknown.state == ProviderArtifactState.UNKNOWN
    assert unknown.unknown_reason == "WRITE_OUTCOME_UNKNOWN"
    assert storage.write_count == 1


def test_exact_replay_returns_the_stored_reference_and_mismatch_is_rejected() -> None:
    factory, storage, service, _ = _services()
    first = service.store_artifact(
        _artifact(),
        owner=_owner(),
        authorize_intent=_authorize,
    )

    replay = service.store_artifact(
        _artifact(),
        owner=_owner(),
        authorize_intent=_authorize,
    )

    assert replay == first
    assert storage.write_count == 1
    assert _only_row(factory).version == 2

    with pytest.raises(StoragePreconditionError, match="changed on replay"):
        service.store_artifact(
            _artifact(payload=b'{"request":"different"}'),
            owner=_owner(),
            authorize_intent=_authorize,
        )
    assert storage.write_count == 1


@pytest.mark.parametrize(
    ("variant", "expected_reason"),
    [
        ("delete_marker", "DELETE_MARKER"),
        ("multiple", "MULTIPLE_VERSIONS"),
        ("mismatch", "OBJECT_MISMATCH"),
    ],
)
def test_reconciler_keeps_noncanonical_evidence_unknown(
    variant: str,
    expected_reason: str,
) -> None:
    factory, storage, service, reconciler = _services()
    storage.mode = "timeout_after"
    with pytest.raises(StorageUnavailableError):
        service.store_artifact(
            _artifact(),
            owner=_owner(),
            authorize_intent=_authorize,
        )
    row = _only_row(factory)

    if variant == "delete_marker":
        storage.entries = [
            ObjectVersionEntry(
                reference=ObjectReference(
                    location=StorageLocationClass.PROVIDER_RESULT,
                    key=row.key,
                    version_id="delete-1",
                ),
                kind="DELETE_MARKER",
            )
        ]
    elif variant == "multiple":
        original = storage.stats["version-1"]
        duplicate = original.model_copy(
            update={"reference": original.reference.model_copy(update={"version_id": "version-2"})}
        )
        storage.entries.append(ObjectVersionEntry(reference=duplicate.reference, kind="OBJECT"))
        storage.stats["version-2"] = duplicate
    else:
        storage.stats["version-1"] = storage.stats["version-1"].model_copy(
            update={"content_type": "text/plain"}
        )

    writes_before = storage.write_count
    reconciler.reconcile_artifact(
        row.id,
        workspace_id=WORKSPACE_ID,
        page_size=2,
        max_pages=2,
    )

    unknown = _only_row(factory)
    assert unknown.state == ProviderArtifactState.UNKNOWN
    assert unknown.unknown_reason == expected_reason
    assert storage.write_count == writes_before


@pytest.mark.parametrize("phase", ["list", "stat"])
def test_reconciler_propagates_temporary_storage_failure_without_mutating_ledger(
    phase: str,
) -> None:
    factory, storage, service, reconciler = _services()
    storage.mode = "timeout_after"
    with pytest.raises(StorageUnavailableError):
        service.store_artifact(
            _artifact(),
            owner=_owner(),
            authorize_intent=_authorize,
        )
    before = _only_row(factory)
    temporary_error = StorageUnavailableError(f"temporary {phase} failure")
    if phase == "list":
        storage.list_error = temporary_error
    else:
        storage.stat_error = temporary_error

    with pytest.raises(StorageUnavailableError, match=f"temporary {phase} failure"):
        reconciler.reconcile_artifact(
            before.id,
            workspace_id=WORKSPACE_ID,
            page_size=2,
            max_pages=2,
        )

    assert _only_row(factory) == before


def test_reconciler_marks_exact_object_missing_as_integrity_unknown() -> None:
    factory, storage, service, reconciler = _services()
    storage.mode = "timeout_after"
    with pytest.raises(StorageUnavailableError):
        service.store_artifact(
            _artifact(),
            owner=_owner(),
            authorize_intent=_authorize,
        )
    row = _only_row(factory)
    storage.stats.clear()

    reconciler.reconcile_artifact(
        row.id,
        workspace_id=WORKSPACE_ID,
        page_size=2,
        max_pages=2,
    )

    unknown = _only_row(factory)
    assert unknown.state == ProviderArtifactState.UNKNOWN
    assert unknown.unknown_reason == "OBJECT_MISSING"


def test_reconciler_bounds_version_pages_and_candidate_rows() -> None:
    factory, storage, service, reconciler = _services()
    for call_index in range(3):
        storage.mode = "crash_before"
        with pytest.raises(SimulatedCrash):
            service.store_artifact(
                _artifact(call_index=call_index),
                owner=_owner(),
                authorize_intent=_authorize,
            )

    first = min(factory.repository.rows.values(), key=lambda artifact: artifact.id)
    storage.entries = [
        ObjectVersionEntry(
            reference=ObjectReference(
                location=StorageLocationClass.PROVIDER_RESULT,
                key=first.key,
                version_id=f"version-{index}",
            ),
            kind="DELETE_MARKER",
        )
        for index in (1, 2)
    ]
    result = reconciler.reconcile_batch(
        stale_before=NOW,
        limit=2,
        page_size=1,
        max_pages=1,
    )

    assert result.examined == 2
    assert result.next_cursor is not None
    assert storage.list_count == 2
    reconciled = factory.repository.rows[first.id]
    assert reconciled.state == ProviderArtifactState.UNKNOWN
    assert reconciled.unknown_reason == "VERSION_PAGE_LIMIT_EXCEEDED"
