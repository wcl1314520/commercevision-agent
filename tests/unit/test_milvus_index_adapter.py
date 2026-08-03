from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from math import inf, nan
from typing import Any

import pytest
from commercevision_contracts import (
    MilvusCollectionCreateRequestV1,
    MilvusCollectionFieldV1,
    MilvusUpsertRequestV1,
    MilvusVectorIdentityV1,
    MilvusVectorRowV1,
    collection_create_request,
)
from commercevision_domain import CollectionSpec, VectorKind, new_uuid7
from commercevision_retrieval import MilvusVectorIndexAdapter
from pydantic import SecretStr


def _collection_request(*, dimension: int = 4):
    return collection_create_request(
        CollectionSpec.create(
            model_family="qwen3-vl-embedding",
            pinned_revision="2026-06-30",
            dimension=dimension,
            vector_kind=VectorKind.IMAGE,
            schema_version=1,
            index_spec_version="hnsw-cosine-v1",
        )
    )


class _Schema:
    def __init__(self, *, auto_id: bool, enable_dynamic_field: bool) -> None:
        self.auto_id = auto_id
        self.enable_dynamic_field = enable_dynamic_field
        self.fields: list[dict[str, Any]] = []

    def add_field(self, **field: Any) -> None:
        self.fields.append(field)


class _IndexParams:
    def __init__(self) -> None:
        self.indexes: list[dict[str, Any]] = []

    def add_index(self, **index: Any) -> None:
        self.indexes.append(index)


class _CreateClient:
    def __init__(self) -> None:
        self.schema: _Schema | None = None
        self.index_params: _IndexParams | None = None
        self.create_collection_call: dict[str, Any] | None = None
        self.create_index_call: dict[str, Any] | None = None

    def has_collection(self, **kwargs: Any) -> bool:
        return self.schema is not None

    def create_schema(self, **kwargs: Any) -> _Schema:
        return _Schema(**kwargs)

    def prepare_index_params(self) -> _IndexParams:
        return _IndexParams()

    def create_collection(
        self,
        *,
        schema: _Schema,
        **kwargs: Any,
    ) -> None:
        self.schema = schema
        self.create_collection_call = kwargs

    def create_index(
        self,
        *,
        index_params: _IndexParams,
        **kwargs: Any,
    ) -> None:
        self.index_params = index_params
        self.create_index_call = kwargs

    def list_indexes(self, **kwargs: Any) -> list[str]:
        if self.index_params is None:
            return []
        return [self.index_params.indexes[0]["index_name"]]

    def describe_collection(self, **kwargs: Any) -> dict[str, Any]:
        assert self.schema is not None
        fields = []
        for field in self.schema.fields:
            params = {}
            if "max_length" in field:
                params["max_length"] = field["max_length"]
            if "dim" in field:
                params["dim"] = field["dim"]
            fields.append(
                {
                    "name": field["field_name"],
                    "type": field["datatype"],
                    "params": params,
                    "is_primary": field.get("is_primary", False),
                }
            )
        return {
            "auto_id": self.schema.auto_id,
            "enable_dynamic_field": self.schema.enable_dynamic_field,
            "consistency_level": 0,
            "fields": fields,
        }

    def describe_index(self, **kwargs: Any) -> dict[str, Any]:
        assert self.index_params is not None
        return self.index_params.indexes[0]

    def load_collection(self, **kwargs: Any) -> None:
        return None

    def get_load_state(self, **kwargs: Any) -> dict[str, Any]:
        return {"state": "Loaded"}

    def close(self) -> None:
        return None


def test_ensure_collection_creates_exact_versioned_schema_and_index() -> None:
    client = _CreateClient()
    adapter = MilvusVectorIndexAdapter(client=client, timeout_seconds=2)

    adapter.ensure_collection(_collection_request())

    assert client.schema is not None
    assert client.schema.auto_id is False
    assert client.schema.enable_dynamic_field is False
    assert client.schema.fields[0] == {
        "field_name": "milvus_primary_key",
        "datatype": 21,
        "is_primary": True,
        "max_length": 64,
    }
    assert client.schema.fields[-1] == {
        "field_name": "vector",
        "datatype": 101,
        "dim": 4,
    }
    assert client.index_params is not None
    assert client.create_collection_call is not None
    assert client.create_collection_call["retry_times"] == 0
    assert client.create_collection_call["retry_on_rate_limit"] is False
    assert client.create_index_call is not None
    assert client.create_index_call["retry_times"] == 0
    assert client.create_index_call["retry_on_rate_limit"] is False
    assert client.index_params.indexes == [
        {
            "field_name": "vector",
            "index_name": "vector_hnsw_cosine_v1",
            "index_type": "HNSW",
            "metric_type": "COSINE",
            "params": {"M": 16, "efConstruction": 200},
        }
    ]


@pytest.mark.parametrize("malformed", [None, {"fields": [None]}])
def test_malformed_collection_description_is_normalized_to_value_error(
    malformed: object,
) -> None:
    class Client(_CreateClient):
        def describe_collection(self, **kwargs: Any) -> object:
            return malformed

    with pytest.raises(ValueError, match="description returned an invalid result"):
        MilvusVectorIndexAdapter(client=Client(), timeout_seconds=2).ensure_collection(
            _collection_request()
        )


def test_malformed_index_description_is_normalized_to_value_error() -> None:
    class Client(_CreateClient):
        def describe_index(self, **kwargs: Any) -> object:
            return None

    with pytest.raises(ValueError, match="description returned an invalid result"):
        MilvusVectorIndexAdapter(client=Client(), timeout_seconds=2).ensure_collection(
            _collection_request()
        )


@pytest.mark.parametrize("consistency_level", [[], {}])
def test_malformed_consistency_level_is_normalized_without_raw_context(
    consistency_level: object,
) -> None:
    class Client(_CreateClient):
        def describe_collection(self, **kwargs: Any) -> dict[str, Any]:
            description = super().describe_collection(**kwargs)
            description["consistency_level"] = consistency_level
            return description

    with pytest.raises(ValueError, match="description returned an invalid result") as error:
        MilvusVectorIndexAdapter(client=Client(), timeout_seconds=2).ensure_collection(
            _collection_request()
        )

    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "failure_stage",
    ["create_schema", "add_field", "prepare_index_params", "add_index"],
)
def test_local_sdk_builder_failures_are_safely_normalized(failure_stage: str) -> None:
    class FailingSchema(_Schema):
        def add_field(self, **field: Any) -> None:
            if failure_stage == "add_field":
                raise RuntimeError("schema builder token=production-secret")
            super().add_field(**field)

    class FailingIndexParams(_IndexParams):
        def add_index(self, **index: Any) -> None:
            if failure_stage == "add_index":
                raise RuntimeError("index builder token=production-secret")
            super().add_index(**index)

    class Client(_CreateClient):
        def create_schema(self, **kwargs: Any) -> _Schema:
            if failure_stage == "create_schema":
                raise RuntimeError("schema factory token=production-secret")
            return FailingSchema(**kwargs)

        def prepare_index_params(self) -> _IndexParams:
            if failure_stage == "prepare_index_params":
                raise RuntimeError("index factory token=production-secret")
            return FailingIndexParams()

    with pytest.raises(ConnectionError, match="construction failed") as error:
        MilvusVectorIndexAdapter(client=Client(), timeout_seconds=2).ensure_collection(
            _collection_request()
        )

    assert "production-secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


class _InProgressIndexClient(_CreateClient):
    def create_index(
        self,
        *,
        index_params: _IndexParams,
        **kwargs: Any,
    ) -> None:
        assert type(kwargs["timeout"]) is int
        raise TimeoutError("index build remains in progress")


def test_float_timeout_uses_integer_sdk_deadline_for_in_progress_index() -> None:
    adapter = MilvusVectorIndexAdapter(
        client=_InProgressIndexClient(),
        timeout_seconds=0.25,
    )

    with pytest.raises(TimeoutError, match="outcome is unknown"):
        adapter.ensure_collection(_collection_request())


class _InProgressLoadClient(_CreateClient):
    def load_collection(self, **kwargs: Any) -> None:
        assert type(kwargs["timeout"]) is int
        raise TimeoutError("collection load remains in progress")


def test_float_timeout_uses_integer_sdk_deadline_for_in_progress_load() -> None:
    adapter = MilvusVectorIndexAdapter(
        client=_InProgressLoadClient(),
        timeout_seconds=0.25,
    )

    with pytest.raises(TimeoutError, match="outcome is unknown"):
        adapter.ensure_collection(_collection_request())


def test_existing_collection_with_schema_drift_fails_closed() -> None:
    client = _CreateClient()
    adapter = MilvusVectorIndexAdapter(client=client, timeout_seconds=2)
    adapter.ensure_collection(_collection_request())
    assert client.schema is not None
    client.schema.fields[-1]["dim"] = 8

    with pytest.raises(ValueError, match="schema does not match"):
        adapter.ensure_collection(_collection_request())


def test_collection_request_cannot_replace_the_varchar_generation_pk() -> None:
    client = _CreateClient()
    adapter = MilvusVectorIndexAdapter(client=client, timeout_seconds=2)
    request = _collection_request()
    incompatible = MilvusCollectionCreateRequestV1(
        **(
            request.model_dump()
            | {
                "fields": [
                    MilvusCollectionFieldV1(
                        name="milvus_primary_key",
                        data_type="INT64",
                        primary_key=True,
                    ),
                    *request.fields[1:],
                ]
            }
        )
    )

    with pytest.raises(ValueError, match="schema is not supported"):
        adapter.ensure_collection(incompatible)

    assert client.schema is None


class _ConcurrentCreateClient(_CreateClient):
    def __init__(self) -> None:
        super().__init__()
        self._barrier = threading.Barrier(2)
        self._create_lock = threading.Lock()
        self.created_count = 0

    def has_collection(self, **kwargs: Any) -> bool:
        exists = super().has_collection(**kwargs)
        if not exists:
            self._barrier.wait(timeout=2)
        return exists

    def create_collection(
        self,
        *,
        schema: _Schema,
        **kwargs: Any,
    ) -> None:
        with self._create_lock:
            if self.schema is not None:
                raise RuntimeError("already exists token=production-secret")
            super().create_collection(schema=schema, **kwargs)
            self.created_count += 1

    def create_index(
        self,
        *,
        index_params: _IndexParams,
        **kwargs: Any,
    ) -> None:
        with self._create_lock:
            if self.index_params is not None:
                raise RuntimeError("already exists token=production-secret")
            super().create_index(index_params=index_params, **kwargs)


def test_concurrent_ensure_is_idempotent_only_after_exact_describe() -> None:
    client = _ConcurrentCreateClient()
    adapters = [
        MilvusVectorIndexAdapter(client=client, timeout_seconds=2),
        MilvusVectorIndexAdapter(client=client, timeout_seconds=2),
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda adapter: adapter.ensure_collection(_collection_request()),
                adapters,
            )
        )

    assert outcomes == [None, None]
    assert client.created_count == 1


def _upsert_request(
    *,
    collection_name: str,
    embedding_record_id: str | None = None,
    generation: int = 2,
) -> MilvusUpsertRequestV1:
    record_id = embedding_record_id or new_uuid7()
    return MilvusUpsertRequestV1(
        collection_name=collection_name,
        row=MilvusVectorRowV1(
            embedding_record_id=record_id,
            milvus_primary_key=f"{record_id}:g{generation}",
            asset_version_id=new_uuid7(),
            workspace_id="workspace-index",
            rights_record_version=3,
            category="APPAREL",
            brand="Example",
            asset_role="HERO",
            vector_kind=VectorKind.IMAGE,
            model_configuration_version="embedding-config-v1",
            input_hash="a" * 64,
            embedding_spec_sha256="b" * 64,
            write_generation=generation,
            indexed_at_epoch_micros=1_785_456_000_000_000,
            vector=[0.1, 0.2, 0.3, 0.4],
        ),
    )


def _identity(request: MilvusUpsertRequestV1) -> MilvusVectorIdentityV1:
    return MilvusVectorIdentityV1(
        collection_name=request.collection_name,
        embedding_record_id=request.row.embedding_record_id,
        milvus_primary_key=request.row.milvus_primary_key,
        input_hash=request.row.input_hash,
        embedding_spec_sha256=request.row.embedding_spec_sha256,
        write_generation=request.row.write_generation,
    )


class _VectorClient(_CreateClient):
    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, dict[str, Any]] = {}
        self.last_query: dict[str, Any] | None = None
        self.last_upsert: dict[str, Any] | None = None
        self.last_delete: dict[str, Any] | None = None
        self.flush_count = 0

    def upsert(self, **kwargs: Any) -> dict[str, Any]:
        self.last_upsert = kwargs
        row = kwargs["data"][0]
        self.rows[row["milvus_primary_key"]] = row
        return {"upsert_count": 1}

    def flush(self, **kwargs: Any) -> None:
        self.flush_count += 1

    def query(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.last_query = kwargs
        return [row for primary_key, row in self.rows.items() if primary_key in kwargs["filter"]]

    def delete(self, **kwargs: Any) -> dict[str, Any]:
        self.last_delete = kwargs
        deleted = 0
        for primary_key in kwargs["ids"]:
            if self.rows.pop(primary_key, None) is not None:
                deleted += 1
        return {"delete_count": deleted}


def test_malformed_proof_row_is_normalized_without_raw_value_or_context() -> None:
    class SecretValue:
        def __repr__(self) -> str:
            return "production-secret"

    class Client(_VectorClient):
        def query(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "embedding_record_id": request.row.embedding_record_id,
                    "milvus_primary_key": request.row.milvus_primary_key,
                    "input_hash": SecretValue(),
                    "embedding_spec_sha256": request.row.embedding_spec_sha256,
                    "write_generation": request.row.write_generation,
                }
            ]

    request = _upsert_request(collection_name=_collection_request().collection_name)
    adapter = MilvusVectorIndexAdapter(client=Client(), timeout_seconds=2)

    with pytest.raises(ValueError, match="proof returned an invalid result") as error:
        adapter.delete_if_generation(_identity(request))

    assert "production-secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_upsert_proves_the_exact_record_input_spec_and_generation() -> None:
    client = _VectorClient()
    adapter = MilvusVectorIndexAdapter(client=client, timeout_seconds=0.25)
    collection = _collection_request()
    adapter.ensure_collection(collection)
    request = _upsert_request(collection_name=collection.collection_name)

    adapter.upsert(request)
    proof = adapter.prove(_identity(request))

    assert proof.matches(_identity(request))
    assert client.flush_count == 0
    assert client.last_upsert is not None
    assert client.last_upsert["data"] == [request.row.model_dump(mode="json")]
    assert client.last_upsert["timeout"] == 1
    assert type(client.last_upsert["timeout"]) is int
    assert client.last_upsert["retry_times"] == 0
    assert client.last_upsert["retry_on_rate_limit"] is False
    assert client.last_query is not None
    assert client.last_query["filter"] == (
        f'milvus_primary_key == "{request.row.milvus_primary_key}"'
    )
    assert client.last_query["consistency_level"] == "Strong"
    assert client.last_query["timeout"] == 1
    assert type(client.last_query["timeout"]) is int
    assert client.last_query["retry_times"] == 0
    assert client.last_query["retry_on_rate_limit"] is False


def test_prove_reports_conflict_when_logical_record_does_not_match_exact_pk() -> None:
    client = _VectorClient()
    adapter = MilvusVectorIndexAdapter(client=client, timeout_seconds=2)
    collection = _collection_request()
    adapter.ensure_collection(collection)
    request = _upsert_request(collection_name=collection.collection_name)
    adapter.upsert(request)
    client.rows[request.row.milvus_primary_key]["embedding_record_id"] = new_uuid7()

    proof = adapter.prove(_identity(request))

    assert proof.exists is True
    assert proof.matches(_identity(request)) is False


def test_delete_if_generation_only_deletes_the_exact_generation_pk() -> None:
    client = _VectorClient()
    adapter = MilvusVectorIndexAdapter(client=client, timeout_seconds=0.25)
    collection = _collection_request()
    adapter.ensure_collection(collection)
    record_id = new_uuid7()
    old_request = _upsert_request(
        collection_name=collection.collection_name,
        embedding_record_id=record_id,
        generation=2,
    )
    new_request = _upsert_request(
        collection_name=collection.collection_name,
        embedding_record_id=record_id,
        generation=3,
    )
    adapter.upsert(old_request)
    adapter.upsert(new_request)

    assert adapter.delete_if_generation(_identity(old_request)) is True
    assert adapter.prove(_identity(old_request)).exists is False
    assert adapter.prove(_identity(new_request)).matches(_identity(new_request))
    assert client.last_delete is not None
    assert client.last_delete["ids"] == [old_request.row.milvus_primary_key]
    assert client.last_delete["timeout"] == 1
    assert type(client.last_delete["timeout"]) is int


def test_delete_if_generation_fails_closed_on_exact_pk_identity_conflict() -> None:
    client = _VectorClient()
    adapter = MilvusVectorIndexAdapter(client=client, timeout_seconds=2)
    collection = _collection_request()
    adapter.ensure_collection(collection)
    request = _upsert_request(collection_name=collection.collection_name)
    adapter.upsert(request)
    client.rows[request.row.milvus_primary_key]["input_hash"] = "f" * 64

    with pytest.raises(ValueError, match="identity conflict"):
        adapter.delete_if_generation(_identity(request))

    assert client.last_delete is None
    assert adapter.prove(_identity(request)).exists is True


def test_close_relies_on_milvus_wal_and_does_not_start_a_shutdown_flush() -> None:
    client = _VectorClient()
    adapter = MilvusVectorIndexAdapter(
        client=client,
        timeout_seconds=2,
        readiness_timeout_seconds=0.25,
    )
    collection = _collection_request()
    request = _upsert_request(collection_name=collection.collection_name)
    adapter.upsert(request)
    adapter.upsert(request)

    adapter.close()

    assert client.flush_count == 0


class _UnknownUpsertClient(_VectorClient):
    def upsert(self, **kwargs: Any) -> dict[str, Any]:
        super().upsert(**kwargs)
        return {"acknowledged": True}


def test_unknown_upsert_result_requires_exact_reconciliation() -> None:
    client = _UnknownUpsertClient()
    adapter = MilvusVectorIndexAdapter(client=client, timeout_seconds=2)
    request = _upsert_request(collection_name=_collection_request().collection_name)

    with pytest.raises(TimeoutError, match="outcome is unknown"):
        adapter.upsert(request)


class _SecretTimeoutClient(_VectorClient):
    def upsert(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Deadline Exceeded for http://root:production-secret@milvus.internal")


def test_sdk_failures_are_normalized_without_secrets_or_endpoints() -> None:
    secret = "production-secret"
    client = _SecretTimeoutClient()
    adapter = MilvusVectorIndexAdapter(client=client, timeout_seconds=2)
    request = _upsert_request(collection_name=_collection_request().collection_name)

    with pytest.raises(TimeoutError) as error:
        adapter.upsert(request)

    assert secret not in str(error.value)
    assert "milvus.internal" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


class _LifecycleClient(_VectorClient):
    def __init__(self) -> None:
        super().__init__()
        self.close_count = 0
        self.readiness_timeout: float | None = None

    def has_collection(self, **kwargs: Any) -> bool:
        self.readiness_timeout = kwargs["timeout"]
        return False

    def close(self) -> None:
        self.close_count += 1


def test_client_creation_is_lazy_and_readiness_close_are_bounded() -> None:
    created: list[dict[str, Any]] = []
    client = _LifecycleClient()

    def factory(**kwargs: Any) -> _LifecycleClient:
        created.append(kwargs)
        return client

    adapter = MilvusVectorIndexAdapter(
        uri="http://127.0.0.1:19531",
        token=SecretStr("root:production-secret"),
        timeout_seconds=2,
        readiness_timeout_seconds=0.25,
        client_factory=factory,
    )
    assert created == []

    adapter.assert_ready()
    assert client.readiness_timeout == 1
    assert created == [
        {
            "uri": "http://127.0.0.1:19531",
            "token": "root:production-secret",
            "db_name": "default",
            "timeout": 1,
        }
    ]

    adapter.close()
    adapter.close()
    assert client.close_count == 1
    with pytest.raises(ConnectionError, match="closed"):
        adapter.assert_ready()


@pytest.mark.parametrize("timeout", [True, 0, -1, inf, nan, 61])
def test_operation_timeout_is_strictly_bounded(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        MilvusVectorIndexAdapter(client=_LifecycleClient(), timeout_seconds=timeout)


class _CloseFailureClient(_LifecycleClient):
    def close(self) -> None:
        raise RuntimeError("close failed with production-secret")


def test_close_failure_is_normalized_without_leaking_secrets() -> None:
    client = _CloseFailureClient()
    adapter = MilvusVectorIndexAdapter(client=client, timeout_seconds=2)

    with pytest.raises(ConnectionError) as error:
        adapter.close()

    assert "production-secret" not in str(error.value)
