"""Milvus 2.4 vector-index adapter."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from importlib.util import find_spec
from math import ceil, isfinite
from time import monotonic
from types import ModuleType
from typing import Any

from commercevision_contracts import (
    MilvusCollectionCreateRequestV1,
    MilvusUpsertRequestV1,
    MilvusVectorIdentityV1,
    MilvusVectorProofV1,
)
from pydantic import SecretStr

_DATA_TYPES = {
    "INT64": 5,
    "VARCHAR": 21,
    "FLOAT_VECTOR": 101,
}
_SCALAR_FIELDS = (
    ("milvus_primary_key", "VARCHAR", True, 64),
    ("embedding_record_id", "VARCHAR", False, 36),
    ("asset_version_id", "VARCHAR", False, 36),
    ("workspace_id", "VARCHAR", False, 128),
    ("rights_record_version", "INT64", False, None),
    ("category", "VARCHAR", False, 128),
    ("brand", "VARCHAR", False, 128),
    ("asset_role", "VARCHAR", False, 64),
    ("vector_kind", "VARCHAR", False, 32),
    ("model_configuration_version", "VARCHAR", False, 128),
    ("input_hash", "VARCHAR", False, 64),
    ("embedding_spec_sha256", "VARCHAR", False, 64),
    ("write_generation", "INT64", False, None),
    ("indexed_at_epoch_micros", "INT64", False, None),
)
_INDEX_SPECS = {
    "hnsw-cosine-v1": {
        "index_name": "vector_hnsw_cosine_v1",
        "M": 16,
        "efConstruction": 200,
    }
}


def _install_pymilvus_24_compatibility() -> None:
    """Provide the two removed pkg_resources APIs used by PyMilvus 2.4."""
    if find_spec("pkg_resources") is not None:
        return
    compatibility = ModuleType("pkg_resources")
    compatibility.DistributionNotFound = PackageNotFoundError
    compatibility.get_distribution = distribution
    sys.modules["pkg_resources"] = compatibility


_install_pymilvus_24_compatibility()


@dataclass(frozen=True)
class _Deadline:
    expires_at: float

    @classmethod
    def start(cls, timeout_seconds: float) -> _Deadline:
        return cls(expires_at=monotonic() + timeout_seconds)

    def sdk_timeout(self) -> int:
        remaining = self.expires_at - monotonic()
        if remaining <= 0:
            raise TimeoutError("Milvus operation deadline expired")
        # PyMilvus 2.4 only applies its retry deadline when timeout is an int.
        return max(1, ceil(remaining))


class MilvusVectorIndexAdapter:
    """Production vector-index boundary backed by a controlled Milvus client."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        client: Any | None = None,
        uri: str | None = None,
        token: SecretStr | None = None,
        db_name: str = "default",
        readiness_timeout_seconds: float = 1.0,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._validate_timeout(timeout_seconds, maximum=60)
        self._validate_timeout(readiness_timeout_seconds, maximum=10)
        if client is None and not uri:
            raise ValueError("Milvus URI is required when no client is injected")
        if client is not None and (uri is not None or client_factory is not None):
            raise ValueError("injected Milvus client cannot be combined with client configuration")
        self._client = client
        self._uri = uri
        self._token = token
        self._db_name = db_name
        self._client_factory = client_factory
        self._timeout_seconds = timeout_seconds
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._lifecycle_lock = threading.Lock()
        self._closed = False

    def ensure_collection(self, request: MilvusCollectionCreateRequestV1) -> None:
        deadline = _Deadline.start(self._timeout_seconds)
        self._validate_collection_request(request)
        try:
            index_spec = _INDEX_SPECS[request.index_spec_version]
        except KeyError as exc:
            raise ValueError("unsupported Milvus index specification") from exc
        collection_exists = self._call(
            "collection existence check",
            "has_collection",
            deadline=deadline,
            collection_name=request.collection_name,
        )
        if not collection_exists:
            schema = self._build_schema(request, deadline)
            try:
                self._call(
                    "collection creation",
                    "create_collection",
                    deadline=deadline,
                    collection_name=request.collection_name,
                    schema=schema,
                    consistency_level="Strong",
                )
            except (TimeoutError, ConnectionError):
                if not self._call(
                    "collection existence check",
                    "has_collection",
                    deadline=deadline,
                    collection_name=request.collection_name,
                ):
                    raise
        self._verify_schema(request, deadline)
        index_names = self._list_indexes(request.collection_name, deadline)
        expected_index_name = index_spec["index_name"]
        if not index_names:
            index_params = self._build_index_params(request, index_spec, deadline)
            try:
                self._call(
                    "index creation",
                    "create_index",
                    deadline=deadline,
                    collection_name=request.collection_name,
                    index_params=index_params,
                )
            except (TimeoutError, ConnectionError):
                if expected_index_name not in self._list_indexes(
                    request.collection_name,
                    deadline,
                ):
                    raise
        elif index_names != [expected_index_name]:
            raise ValueError("Milvus collection index does not match the requested specification")
        self._verify_index(request, index_spec, deadline)
        self._call(
            "collection load",
            "load_collection",
            deadline=deadline,
            collection_name=request.collection_name,
            replica_number=1,
        )

    def upsert(self, request: MilvusUpsertRequestV1) -> None:
        deadline = _Deadline.start(self._timeout_seconds)
        self._require_generation_key(
            embedding_record_id=request.row.embedding_record_id,
            primary_key=request.row.milvus_primary_key,
            write_generation=request.row.write_generation,
        )
        result = self._call(
            "upsert",
            "upsert",
            deadline=deadline,
            outcome_unknown=True,
            collection_name=request.collection_name,
            data=[request.row.model_dump(mode="json")],
        )
        if not isinstance(result, dict) or result.get("upsert_count") != 1:
            raise TimeoutError("Milvus upsert outcome is unknown")

    def prove(self, identity: MilvusVectorIdentityV1) -> MilvusVectorProofV1:
        return self._prove(identity, _Deadline.start(self._timeout_seconds))

    def _prove(
        self,
        identity: MilvusVectorIdentityV1,
        deadline: _Deadline,
    ) -> MilvusVectorProofV1:
        self._require_generation_key(
            embedding_record_id=identity.embedding_record_id,
            primary_key=identity.milvus_primary_key,
            write_generation=identity.write_generation,
        )
        rows = self._call(
            "proof query",
            "query",
            deadline=deadline,
            collection_name=identity.collection_name,
            filter=f"milvus_primary_key == {json.dumps(identity.milvus_primary_key)}",
            output_fields=[
                "milvus_primary_key",
                "embedding_record_id",
                "input_hash",
                "embedding_spec_sha256",
                "write_generation",
            ],
            consistency_level="Strong",
        )
        if not isinstance(rows, list):
            raise ValueError("Milvus proof returned an invalid result")
        if not rows:
            return MilvusVectorProofV1(exists=False)
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("Milvus proof returned an invalid result")
        row = rows[0]
        logical_record_matches = row.get("embedding_record_id") == identity.embedding_record_id
        invalid_proof = False
        try:
            proof = MilvusVectorProofV1(
                exists=True,
                milvus_primary_key=(
                    row.get("milvus_primary_key") if logical_record_matches else None
                ),
                input_hash=row.get("input_hash"),
                embedding_spec_sha256=row.get("embedding_spec_sha256"),
                write_generation=row.get("write_generation"),
            )
        except (TypeError, ValueError):
            invalid_proof = True
        if invalid_proof:
            raise ValueError("Milvus proof returned an invalid result")
        return proof

    def delete_if_generation(self, identity: MilvusVectorIdentityV1) -> bool:
        deadline = _Deadline.start(self._timeout_seconds)
        proof = self._prove(identity, deadline)
        if not proof.exists:
            return False
        if not proof.matches(identity):
            raise ValueError("Milvus delete identity conflict")
        result = self._call(
            "delete",
            "delete",
            deadline=deadline,
            outcome_unknown=True,
            collection_name=identity.collection_name,
            ids=[identity.milvus_primary_key],
        )
        if not isinstance(result, dict):
            raise TimeoutError("Milvus delete outcome is unknown")
        deleted = result.get("delete_count", result.get("delete_cnt"))
        if deleted != 1:
            if deleted == 0:
                return False
            raise TimeoutError("Milvus delete outcome is unknown")
        return True

    def assert_ready(self) -> None:
        deadline = _Deadline.start(self._readiness_timeout_seconds)
        self._call(
            "readiness check",
            "has_collection",
            deadline=deadline,
            collection_name="cv_readiness_probe",
        )

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            client = self._client
        if client is None:
            return
        try:
            # Milvus writes are WAL-backed; client.close only disconnects the local channel.
            client.close()
        except Exception:
            raise ConnectionError("Milvus client close failed") from None

    def _get_client(self, deadline: _Deadline | None = None) -> Any:
        with self._lifecycle_lock:
            if self._closed:
                raise ConnectionError("Milvus adapter is closed")
            if self._client is not None:
                return self._client
            factory = self._client_factory
            if factory is None:
                try:
                    from pymilvus import MilvusClient
                except Exception:
                    raise ConnectionError("Milvus SDK is unavailable") from None
                factory = MilvusClient
            kwargs: dict[str, Any] = {
                "uri": self._uri,
                "db_name": self._db_name,
                "timeout": (
                    deadline.sdk_timeout()
                    if deadline is not None
                    else max(1, ceil(self._timeout_seconds))
                ),
            }
            if self._token is not None:
                kwargs["token"] = self._token.get_secret_value()
            try:
                self._client = factory(**kwargs)
            except Exception:
                raise ConnectionError("Milvus client initialization failed") from None
            return self._client

    def _call(
        self,
        operation: str,
        method_name: str,
        *,
        deadline: _Deadline,
        outcome_unknown: bool = False,
        **kwargs: Any,
    ) -> Any:
        client = self._get_client(deadline)
        try:
            kwargs["timeout"] = deadline.sdk_timeout()
            kwargs["retry_times"] = 0
            kwargs["retry_on_rate_limit"] = False
            return getattr(client, method_name)(**kwargs)
        except Exception as exc:
            exception_name = type(exc).__name__.lower()
            exception_text = str(exc).lower()
            is_timeout = (
                isinstance(exc, TimeoutError)
                or "timeout" in exception_name
                or "deadline" in exception_name
                or "deadline" in exception_text
            )
            if outcome_unknown or is_timeout:
                raise TimeoutError(f"Milvus {operation} outcome is unknown") from None
            raise ConnectionError(f"Milvus {operation} failed") from None

    def _build_schema(
        self,
        request: MilvusCollectionCreateRequestV1,
        deadline: _Deadline,
    ) -> Any:
        client = self._get_client(deadline)
        schema: Any | None = None
        construction_failed = False
        try:
            schema = client.create_schema(
                auto_id=False,
                enable_dynamic_field=False,
            )
            for field in request.fields:
                definition: dict[str, Any] = {
                    "field_name": field.name,
                    "datatype": _DATA_TYPES[field.data_type],
                }
                if field.primary_key:
                    definition["is_primary"] = True
                if field.maximum_length is not None:
                    definition["max_length"] = field.maximum_length
                if field.dimension is not None:
                    definition["dim"] = field.dimension
                schema.add_field(**definition)
        except Exception:
            construction_failed = True
        if construction_failed or schema is None:
            raise ConnectionError("Milvus schema construction failed")
        return schema

    def _build_index_params(
        self,
        request: MilvusCollectionCreateRequestV1,
        index_spec: dict[str, Any],
        deadline: _Deadline,
    ) -> Any:
        client = self._get_client(deadline)
        index_params: Any | None = None
        construction_failed = False
        try:
            index_params = client.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_name=index_spec["index_name"],
                index_type=request.index_type,
                metric_type=request.metric_type,
                params={
                    "M": index_spec["M"],
                    "efConstruction": index_spec["efConstruction"],
                },
            )
        except Exception:
            construction_failed = True
        if construction_failed or index_params is None:
            raise ConnectionError("Milvus index parameter construction failed")
        return index_params

    @staticmethod
    def _validate_collection_request(request: MilvusCollectionCreateRequestV1) -> None:
        actual_scalars = tuple(
            (
                field.name,
                field.data_type,
                field.primary_key,
                field.maximum_length,
            )
            for field in request.fields[:-1]
        )
        vector = request.fields[-1]
        if (
            actual_scalars != _SCALAR_FIELDS
            or vector.name != "vector"
            or vector.data_type != "FLOAT_VECTOR"
            or vector.primary_key
            or vector.maximum_length is not None
            or vector.dimension is None
        ):
            raise ValueError("Milvus collection schema is not supported")

    @staticmethod
    def _validate_timeout(value: float, *, maximum: float) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or not 0 < value <= maximum
        ):
            raise ValueError("Milvus timeout must be finite and strictly bounded")

    @staticmethod
    def _require_generation_key(
        *,
        embedding_record_id: str,
        primary_key: str,
        write_generation: int,
    ) -> None:
        if primary_key != f"{embedding_record_id}:g{write_generation}":
            raise ValueError("Milvus primary key does not match the record generation")

    def _list_indexes(
        self,
        collection_name: str,
        deadline: _Deadline,
    ) -> list[str]:
        indexes = self._call(
            "index listing",
            "list_indexes",
            deadline=deadline,
            collection_name=collection_name,
        )
        if not isinstance(indexes, list) or any(not isinstance(name, str) for name in indexes):
            raise ValueError("Milvus index listing returned an invalid result")
        return sorted(indexes)

    def _verify_schema(
        self,
        request: MilvusCollectionCreateRequestV1,
        deadline: _Deadline,
    ) -> None:
        description = self._call(
            "collection description",
            "describe_collection",
            deadline=deadline,
            collection_name=request.collection_name,
        )
        if not isinstance(description, dict):
            raise ValueError("Milvus collection description returned an invalid result")
        described_fields = description.get("fields")
        if not isinstance(described_fields, list) or any(
            not isinstance(field, dict) for field in described_fields
        ):
            raise ValueError("Milvus collection description returned an invalid result")
        expected_fields = [
            {
                "name": field.name,
                "type": _DATA_TYPES[field.data_type],
                "params": {
                    **(
                        {"max_length": field.maximum_length}
                        if field.maximum_length is not None
                        else {}
                    ),
                    **({"dim": field.dimension} if field.dimension is not None else {}),
                },
                "is_primary": field.primary_key,
            }
            for field in request.fields
        ]
        actual_fields: list[dict[str, Any]] = []
        invalid_fields = False
        try:
            actual_fields = [
                {
                    "name": field.get("name"),
                    "type": int(field.get("type")),
                    "params": {
                        key: int(value)
                        for key, value in field.get("params", {}).items()
                        if key in {"max_length", "dim"}
                    },
                    "is_primary": bool(field.get("is_primary", False)),
                }
                for field in described_fields
            ]
        except (AttributeError, TypeError, ValueError):
            invalid_fields = True
        consistency_level = description.get("consistency_level")
        if invalid_fields or (
            isinstance(consistency_level, bool) or not isinstance(consistency_level, (int, str))
        ):
            raise ValueError("Milvus collection description returned an invalid result")
        if (
            description.get("auto_id") is not False
            or description.get("enable_dynamic_field") is not False
            or consistency_level not in {0, "Strong"}
            or actual_fields != expected_fields
        ):
            raise ValueError("Milvus collection schema does not match the requested specification")

    def _verify_index(
        self,
        request: MilvusCollectionCreateRequestV1,
        index_spec: dict[str, Any],
        deadline: _Deadline,
    ) -> None:
        described_index = self._call(
            "index description",
            "describe_index",
            deadline=deadline,
            collection_name=request.collection_name,
            index_name=index_spec["index_name"],
        )
        if not isinstance(described_index, dict):
            raise ValueError("Milvus index description returned an invalid result")
        index_params = described_index.get("params", {})
        if not isinstance(index_params, dict):
            raise ValueError("Milvus index description returned an invalid result")
        actual_index: dict[str, Any] = {}
        invalid_index = False
        try:
            actual_index = {
                "field_name": described_index.get("field_name"),
                "index_name": described_index.get("index_name"),
                "index_type": described_index.get("index_type"),
                "metric_type": described_index.get("metric_type"),
                "M": int(described_index.get("M", index_params.get("M", -1))),
                "efConstruction": int(
                    described_index.get(
                        "efConstruction",
                        index_params.get("efConstruction", -1),
                    )
                ),
            }
        except (TypeError, ValueError):
            invalid_index = True
        if invalid_index:
            raise ValueError("Milvus index description returned an invalid result")
        expected_index = {
            "field_name": "vector",
            "index_name": index_spec["index_name"],
            "index_type": request.index_type,
            "metric_type": request.metric_type,
            "M": index_spec["M"],
            "efConstruction": index_spec["efConstruction"],
        }
        if actual_index != expected_index:
            raise ValueError("Milvus collection index does not match the requested specification")
