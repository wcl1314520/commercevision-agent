"""Renewable Alibaba Cloud credential bridge for the OSS SDK."""

from __future__ import annotations

from concurrent.futures import CancelledError, Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Lock, Thread
from time import monotonic
from typing import Protocol

import oss2
from alibabacloud_credentials.client import Client as AlibabaCredentialsClient
from alibabacloud_credentials.exceptions import CredentialException
from alibabacloud_credentials.models import Config, CredentialModel
from commercevision_contracts import Settings
from commercevision_domain import StorageUnavailableError
from Tea.exceptions import RetryError, TeaException, ValidateException


class AlibabaCredentialClient(Protocol):
    def get_credential(self) -> CredentialModel: ...


class AlibabaCloudCredentialsProvider(oss2.credentials.CredentialsProvider):
    """Adapt the refreshing Alibaba credential client to oss2 ProviderAuthV4."""

    def __init__(
        self,
        client: AlibabaCredentialClient,
        *,
        refresh_timeout_seconds: float = 5.0,
    ) -> None:
        if refresh_timeout_seconds <= 0:
            raise ValueError("credential refresh timeout must be positive")
        self._client = client
        self._refresh_timeout_seconds = refresh_timeout_seconds
        self._refresh_lock = Lock()
        self._state_lock = Lock()
        self._inflight: Future[CredentialModel] | None = None
        self._closed = False

    def get_credentials(self) -> oss2.credentials.Credentials:
        deadline = monotonic() + self._refresh_timeout_seconds
        if not self._refresh_lock.acquire(timeout=self._refresh_timeout_seconds):
            raise StorageUnavailableError("OSS workload identity credential refresh timed out")
        try:
            with self._state_lock:
                if self._closed:
                    raise StorageUnavailableError(
                        "OSS workload identity credential provider is closed"
                    )
                future = self._inflight
                if future is None:
                    future = Future()
                    self._inflight = future
                    refresh_thread = Thread(
                        target=self._refresh,
                        args=(future,),
                        name="oss-credential-refresh",
                        daemon=True,
                    )
                    try:
                        refresh_thread.start()
                    except RuntimeError as exc:
                        self._inflight = None
                        raise StorageUnavailableError(
                            "OSS workload identity credential refresh could not start"
                        ) from exc
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise FutureTimeoutError
            credential = future.result(timeout=remaining)
            with self._state_lock:
                access_key_id = credential.access_key_id
                access_key_secret = credential.access_key_secret
                security_token = credential.security_token
                if self._inflight is future:
                    self._inflight = None
        except FutureTimeoutError as exc:
            self._clear_completed_refresh(future)
            raise StorageUnavailableError(
                "OSS workload identity credential refresh timed out"
            ) from exc
        except CancelledError as exc:
            self._clear_completed_refresh(future)
            raise StorageUnavailableError(
                "OSS workload identity credential refresh was cancelled"
            ) from exc
        except (
            CredentialException,
            OSError,
            RetryError,
            TeaException,
            ValidateException,
        ) as exc:
            self._clear_completed_refresh(future)
            raise StorageUnavailableError(
                "OSS workload identity credential refresh failed"
            ) from exc
        except (AttributeError, TypeError, ValueError) as exc:
            self._clear_completed_refresh(future)
            raise StorageUnavailableError(
                "OSS workload identity returned incomplete credentials"
            ) from exc
        finally:
            self._refresh_lock.release()
        if (
            not isinstance(access_key_id, str)
            or not access_key_id.strip()
            or not isinstance(access_key_secret, str)
            or not access_key_secret
            or not isinstance(security_token, str)
            or not security_token
        ):
            raise StorageUnavailableError("OSS workload identity returned incomplete credentials")
        return oss2.credentials.Credentials(
            access_key_id,
            access_key_secret,
            security_token,
        )

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            future = self._inflight
            if future is not None:
                future.cancel()

    def _clear_completed_refresh(self, future: Future[CredentialModel]) -> None:
        with self._state_lock:
            if self._inflight is future and future.done():
                self._inflight = None

    def _refresh(self, future: Future[CredentialModel]) -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            credential = self._client.get_credential()
        except Exception as exc:
            future.set_exception(exc)
        else:
            future.set_result(credential)


def create_oss_credentials_provider(
    settings: Settings,
) -> AlibabaCloudCredentialsProvider:
    """Build one renewable provider without fetching credentials at construction."""

    timeout_ms = max(1, round(settings.object_store_read_timeout_seconds * 1000))
    connect_timeout_ms = max(
        1,
        round(settings.object_store_connect_timeout_seconds * 1000),
    )
    if settings.object_store_credential_mode == "ecs_ram_role":
        config = Config(
            type="ecs_ram_role",
            role_name=settings.object_store_ram_role_name or "",
            timeout=timeout_ms,
            connect_timeout=connect_timeout_ms,
            enable_imds_v2=True,
            disable_imds_v1=True,
        )
    elif settings.object_store_credential_mode == "oidc_role_arn":
        config = Config(
            type="oidc_role_arn",
            role_arn=settings.object_store_oidc_role_arn or "",
            oidc_provider_arn=settings.object_store_oidc_provider_arn or "",
            oidc_token_file_path=settings.object_store_oidc_token_file_path or "",
            role_session_name=settings.object_store_role_session_name,
            sts_endpoint=settings.object_store_sts_endpoint,
            timeout=timeout_ms,
            connect_timeout=connect_timeout_ms,
        )
    else:
        raise ValueError("renewable OSS credentials require a workload identity mode")
    return AlibabaCloudCredentialsProvider(
        AlibabaCredentialsClient(config),
        refresh_timeout_seconds=(settings.object_store_credential_refresh_timeout_seconds),
    )
