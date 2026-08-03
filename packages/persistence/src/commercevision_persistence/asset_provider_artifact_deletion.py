"""Bounded exact-version deletion for immutable Provider Artifact ledger targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from commercevision_contracts.object_storage import (
    ObjectReference,
    ObjectStat,
    ObjectVersionPage,
)
from commercevision_contracts.product_briefs import PreparedProviderArtifact
from commercevision_domain import UploadObjectMissingError


@dataclass(frozen=True, slots=True)
class ProviderArtifactDeletionTarget:
    id: str
    state: str
    target: PreparedProviderArtifact
    provider_version_id: str | None
    etag: str | None


class ProviderArtifactDeletionStore(Protocol):
    def list_versions(
        self,
        target: PreparedProviderArtifact,
        *,
        page_size: int,
        continuation_token: str | None,
    ) -> ObjectVersionPage: ...

    def stat(
        self,
        target: PreparedProviderArtifact,
        reference: ObjectReference,
    ) -> ObjectStat: ...

    def delete_if_match(
        self,
        target: PreparedProviderArtifact,
        reference: ObjectReference,
        *,
        expected_etag: str,
    ) -> bool: ...

    def delete_marker(
        self,
        target: PreparedProviderArtifact,
        reference: ObjectReference,
    ) -> bool: ...


class ProviderArtifactDeletionConverger:
    """Delete STORED exact versions and exhaustively sweep uncertain exact keys."""

    def __init__(
        self,
        *,
        store: ProviderArtifactDeletionStore,
        version_page_size: int,
        max_version_pages: int,
        max_versions: int,
        stable_empty_passes: int,
    ) -> None:
        if version_page_size < 1 or max_version_pages < stable_empty_passes:
            raise ValueError("Provider artifact deletion version scan bounds are invalid")
        if max_versions < 1 or stable_empty_passes < 2:
            raise ValueError("Provider artifact deletion convergence bounds are invalid")
        self._store = store
        self._version_page_size = version_page_size
        self._max_version_pages = max_version_pages
        self._max_versions = max_versions
        self._stable_empty_passes = stable_empty_passes

    def converge(self, artifact: ProviderArtifactDeletionTarget) -> None:
        if artifact.state == "STORED":
            if artifact.provider_version_id is None or artifact.etag is None:
                raise ValueError("STORED Provider Artifact lacks exact object identity")
            self._store.delete_if_match(
                artifact.target,
                ObjectReference(
                    location=artifact.target.location,
                    key=artifact.target.key,
                    version_id=artifact.provider_version_id,
                ),
                expected_etag=artifact.etag,
            )
            return
        self.sweep_uncertain_key(artifact.target)

    def sweep_uncertain_key(self, target: PreparedProviderArtifact) -> None:
        empty_scans = 0
        pages = 0
        versions = 0
        cursor: str | None = None
        while pages < self._max_version_pages:
            page = self._store.list_versions(
                target,
                page_size=self._version_page_size,
                continuation_token=cursor,
            )
            pages += 1
            if not page.entries:
                empty_scans += 1
                cursor = None
                if empty_scans >= self._stable_empty_passes:
                    return
                continue
            empty_scans = 0
            for entry in page.entries:
                versions += 1
                if versions > self._max_versions:
                    raise TimeoutError("Provider artifact deletion exceeded its version budget")
                if entry.kind == "DELETE_MARKER":
                    self._store.delete_marker(target, entry.reference)
                    continue
                try:
                    stat = self._store.stat(target, entry.reference)
                except UploadObjectMissingError:
                    continue
                self._store.delete_if_match(
                    target,
                    entry.reference,
                    expected_etag=stat.etag,
                )
            cursor = page.continuation_token
        raise TimeoutError("Provider artifact deletion exceeded its page budget")
