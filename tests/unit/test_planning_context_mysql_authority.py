from __future__ import annotations

from typing import Any, cast

import pytest
from commercevision_domain import PlanningContextPolicy
from commercevision_persistence import MySqlPlanningContextAuthority
from commercevision_persistence.planning_context_authority import planning_context_citation_id
from sqlalchemy.orm import Session, sessionmaker


def test_policy_registry_is_server_owned_and_citation_identity_is_deterministic() -> None:
    policy = PlanningContextPolicy(
        version="planning-context-v1",
        maximum_tokens=2_000,
        maximum_images=4,
    )
    policies = {policy.version: policy}
    authority = MySqlPlanningContextAuthority(
        cast(sessionmaker[Session], cast(Any, object())),
        policies=policies,
    )
    policies.clear()

    assert authority.load_policy(version="planning-context-v1") == policy
    assert authority.load_policy(version="attacker-policy") is None
    assert (
        planning_context_citation_id("019b0000-0000-7000-8000-000000000701", rank=3)
        == "019b0000-0000-7000-8000-000000000701:3"
    )
    with pytest.raises(ValueError, match="rank"):
        planning_context_citation_id("019b0000-0000-7000-8000-000000000701", rank=0)
