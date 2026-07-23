import pytest
from commercevision_application.operator_ports import AuthenticatedPrincipal
from commercevision_application.operators import DeadLetterOperatorService
from commercevision_domain import NotFoundError

_WORKSPACE_ID = "workspace-dead-letter-unit"
_CANONICAL_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_PRINCIPAL = AuthenticatedPrincipal(
    actor_id="dead-letter-unit-admin",
    workspace_ids=frozenset({_WORKSPACE_ID}),
    admin_workspace_ids=frozenset({_WORKSPACE_ID}),
)


class _AllowAllAccess:
    def require_workspace(self, **_kwargs: object) -> None:
        return None

    def require_admin(self, **_kwargs: object) -> None:
        return None

    def require_system_admin(self, **_kwargs: object) -> None:
        return None


class _UnexpectedUowFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("invalid dead-letter identity reached persistence")


class _RecordingDeadLetters:
    def __init__(self) -> None:
        self.lookups: list[tuple[str, str, bool]] = []
        self.legacy_lookups: list[str] = []

    def get_by_id(
        self,
        *,
        workspace_id: str,
        dead_letter_id: str,
        for_update: bool = False,
    ):
        self.lookups.append((workspace_id, dead_letter_id, for_update))
        return None

    def get_legacy(self, *, dead_letter_id: str):
        self.legacy_lookups.append(dead_letter_id)
        return None


class _RecordingUow:
    def __init__(self, dead_letters: _RecordingDeadLetters) -> None:
        self.dead_letters = dead_letters

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _invalid_ids() -> tuple[str, ...]:
    return (
        _CANONICAL_ID.replace("a", "\u00e1", 1),
        _CANONICAL_ID.replace("a", "a\u0301", 1),
        _CANONICAL_ID.replace("a", "\uff41", 1),
        _CANONICAL_ID[:1] + "\u200b" + _CANONICAL_ID[1:],
        "not-a-uuid",
        f" {_CANONICAL_ID}",
        f"{_CANONICAL_ID} ",
        f"{_CANONICAL_ID}a",
    )


@pytest.mark.parametrize("dead_letter_id", _invalid_ids())
def test_application_rejects_noncanonical_dead_letter_ids_before_uow(
    dead_letter_id: str,
) -> None:
    factory = _UnexpectedUowFactory()
    service = DeadLetterOperatorService(
        uow_factory=factory,
        access_policy=_AllowAllAccess(),
    )

    with pytest.raises(NotFoundError, match="^dead letter was not found$"):
        service.get(
            workspace_id=_WORKSPACE_ID,
            dead_letter_id=dead_letter_id,
            principal=_PRINCIPAL,
        )
    with pytest.raises(NotFoundError, match="^dead letter was not found$"):
        service.replay(
            workspace_id=_WORKSPACE_ID,
            dead_letter_id=dead_letter_id,
            principal=_PRINCIPAL,
            reason="strict application boundary",
            idempotency_key="strict-application-boundary",
            trace_id="trace-strict-application-boundary",
        )
    with pytest.raises(NotFoundError, match="^dead letter was not found$"):
        service.get_legacy(
            dead_letter_id=dead_letter_id,
            principal=_PRINCIPAL,
        )

    assert factory.calls == 0


def test_application_queries_with_canonical_lowercase_dead_letter_id() -> None:
    dead_letters = _RecordingDeadLetters()
    service = DeadLetterOperatorService(
        uow_factory=lambda: _RecordingUow(dead_letters),
        access_policy=_AllowAllAccess(),
    )

    with pytest.raises(NotFoundError, match="^dead letter was not found$"):
        service.get(
            workspace_id=_WORKSPACE_ID,
            dead_letter_id=_CANONICAL_ID.upper(),
            principal=_PRINCIPAL,
        )
    with pytest.raises(NotFoundError, match="^dead letter was not found$"):
        service.replay(
            workspace_id=_WORKSPACE_ID,
            dead_letter_id=_CANONICAL_ID.upper(),
            principal=_PRINCIPAL,
            reason="canonical application query",
            idempotency_key="canonical-application-query",
            trace_id="trace-canonical-application-query",
        )
    with pytest.raises(NotFoundError, match="^dead letter was not found$"):
        service.get_legacy(
            dead_letter_id=_CANONICAL_ID.upper(),
            principal=_PRINCIPAL,
        )

    assert dead_letters.lookups == [
        (_WORKSPACE_ID, _CANONICAL_ID, False),
        (_WORKSPACE_ID, _CANONICAL_ID, True),
    ]
    assert dead_letters.legacy_lookups == [_CANONICAL_ID]
