from types import SimpleNamespace

import pytest
from commercevision_api import container as container_module
from commercevision_api.container import ApiContainer


def test_api_container_attempts_every_cleanup_in_reverse_construction_order(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class _Resource:
        def __init__(self, name: str, *, fails: bool = False) -> None:
            self.name = name
            self.fails = fails

        def close(self) -> None:
            calls.append(self.name)
            if self.fails:
                raise RuntimeError(f"{self.name} failed")

    container = object.__new__(ApiContainer)
    container.retrieval_closeables = (
        _Resource("embedding", fails=True),
        _Resource("vector-index"),
    )
    container.object_storage_readiness = SimpleNamespace()
    container.database = SimpleNamespace(dispose=lambda: calls.append("database"))

    def close_storage(_storage) -> None:
        calls.append("object-storage")
        raise RuntimeError("object storage failed")

    monkeypatch.setattr(container_module, "close_object_storage", close_storage)

    with pytest.raises(ExceptionGroup) as raised:
        container.close()

    assert calls == ["vector-index", "embedding", "object-storage", "database"]
    assert [str(error) for error in raised.value.exceptions] == [
        "embedding failed",
        "object storage failed",
    ]
