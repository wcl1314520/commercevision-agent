"""Versioned, score-space-safe retrieval ranking primitives."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .ids import canonicalize_uuid

_POLICY_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class RetrievalChannel(StrEnum):
    IMAGE_DENSE = "IMAGE_DENSE"
    PRODUCT_FUSED_DENSE = "PRODUCT_FUSED_DENSE"
    LEXICAL = "LEXICAL"
    BRAND_PROFILE = "BRAND_PROFILE"
    EXPLICIT = "EXPLICIT"


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    version: str
    rrf_k: int
    channel_weights: Mapping[RetrievalChannel, float]
    maximum_business_adjustment: float

    def __post_init__(self) -> None:
        if _POLICY_VERSION_PATTERN.fullmatch(self.version) is None:
            raise ValueError("retrieval policy version is invalid")
        if type(self.rrf_k) is not int or not 1 <= self.rrf_k <= 10_000:
            raise ValueError("retrieval policy RRF constant is invalid")
        weights = dict(self.channel_weights)
        if set(weights) != set(RetrievalChannel):
            raise ValueError("retrieval policy must weight every candidate channel")
        if any(
            not isinstance(weight, int | float)
            or isinstance(weight, bool)
            or not math.isfinite(weight)
            or not 0 < float(weight) <= 100
            for weight in weights.values()
        ):
            raise ValueError("retrieval policy channel weights are invalid")
        maximum = self.maximum_business_adjustment
        if (
            not isinstance(maximum, int | float)
            or isinstance(maximum, bool)
            or not math.isfinite(maximum)
            or not 0 <= float(maximum) <= 1
        ):
            raise ValueError("retrieval policy business adjustment bound is invalid")
        object.__setattr__(
            self,
            "channel_weights",
            MappingProxyType({channel: float(weight) for channel, weight in weights.items()}),
        )
        object.__setattr__(self, "maximum_business_adjustment", float(maximum))


@dataclass(frozen=True, slots=True)
class ReciprocalRankedCandidate:
    asset_version_id: str
    channel_ranks: Mapping[RetrievalChannel, int]
    rrf_score: float
    business_adjustment: float
    final_score: float


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    asset_id: str
    asset_version_id: str
    content_sha256: str
    required_brand_member: bool = False

    def __post_init__(self) -> None:
        canonicalize_uuid(self.asset_id)
        canonicalize_uuid(self.asset_version_id)
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_sha256
        ):
            raise ValueError("retrieval candidate content hash must be a lowercase SHA-256")
        if not isinstance(self.required_brand_member, bool):
            raise ValueError("required brand member must be a boolean")


def reciprocal_rank_fuse(
    *,
    rankings: Mapping[RetrievalChannel, Sequence[str]],
    policy: RetrievalPolicy,
    business_adjustments: Mapping[str, float] | None = None,
) -> tuple[ReciprocalRankedCandidate, ...]:
    """Fuse channel ranks without accepting incomparable raw score spaces."""

    adjustments = dict(business_adjustments or {})
    channel_ranks: dict[str, dict[RetrievalChannel, int]] = {}
    scores: dict[str, float] = {}
    for channel, ranking in rankings.items():
        if channel not in policy.channel_weights:
            raise ValueError("retrieval ranking contains an unversioned channel")
        if len(set(ranking)) != len(ranking):
            raise ValueError("retrieval channel ranking must contain unique candidates")
        for rank, asset_version_id in enumerate(ranking, start=1):
            canonicalize_uuid(asset_version_id)
            channel_ranks.setdefault(asset_version_id, {})[channel] = rank
            scores[asset_version_id] = scores.get(asset_version_id, 0.0) + (
                policy.channel_weights[channel] / (policy.rrf_k + rank)
            )
    unknown_adjustments = set(adjustments) - set(scores)
    if unknown_adjustments:
        raise ValueError("business adjustment cannot add retrieval candidates")
    results: list[ReciprocalRankedCandidate] = []
    for asset_version_id, rrf_score in scores.items():
        adjustment = adjustments.get(asset_version_id, 0.0)
        if (
            not isinstance(adjustment, int | float)
            or isinstance(adjustment, bool)
            or not math.isfinite(adjustment)
            or abs(float(adjustment)) > policy.maximum_business_adjustment
        ):
            raise ValueError("retrieval business adjustment exceeds its policy bound")
        normalized_adjustment = float(adjustment)
        results.append(
            ReciprocalRankedCandidate(
                asset_version_id=asset_version_id,
                channel_ranks=MappingProxyType(dict(channel_ranks[asset_version_id])),
                rrf_score=rrf_score,
                business_adjustment=normalized_adjustment,
                final_score=rrf_score + normalized_adjustment,
            )
        )
    results.sort(key=lambda candidate: (-candidate.final_score, candidate.asset_version_id))
    return tuple(results)


def apply_bounded_rerank(
    supplied_candidate_ids: Sequence[str],
    reranked_candidate_ids: Sequence[str],
) -> tuple[str, ...]:
    """Accept only an exact permutation of the already-eligible candidate set."""

    supplied = tuple(supplied_candidate_ids)
    reranked = tuple(reranked_candidate_ids)
    if (
        len(set(supplied)) != len(supplied)
        or len(set(reranked)) != len(reranked)
        or set(supplied) != set(reranked)
    ):
        raise ValueError("reranker output must be an exact candidate permutation")
    for candidate_id in supplied:
        canonicalize_uuid(candidate_id)
    return reranked


def deduplicate_retrieval_candidates(
    ranked_candidates: Sequence[RetrievalCandidate],
) -> tuple[RetrievalCandidate, ...]:
    """Deduplicate versions and hashes while retaining every required brand member."""

    required_assets = {
        candidate.asset_id for candidate in ranked_candidates if candidate.required_brand_member
    }
    required_hashes = {
        candidate.content_sha256
        for candidate in ranked_candidates
        if candidate.required_brand_member
    }
    seen_versions: set[str] = set()
    seen_assets: set[str] = set()
    seen_hashes: set[str] = set()
    result: list[RetrievalCandidate] = []
    for candidate in ranked_candidates:
        if candidate.asset_version_id in seen_versions:
            continue
        seen_versions.add(candidate.asset_version_id)
        if candidate.required_brand_member:
            result.append(candidate)
            continue
        if candidate.asset_id in required_assets or candidate.content_sha256 in required_hashes:
            continue
        if candidate.asset_id in seen_assets or candidate.content_sha256 in seen_hashes:
            continue
        seen_assets.add(candidate.asset_id)
        seen_hashes.add(candidate.content_sha256)
        result.append(candidate)
    return tuple(result)


def bound_retrieval_candidates(
    ranked_candidates: Sequence[RetrievalCandidate],
    *,
    limit: int,
) -> tuple[RetrievalCandidate, ...]:
    """Bound the fused set without dropping required Brand Profile members."""

    if type(limit) is not int or limit < 1:
        raise ValueError("retrieval candidate limit must be positive")
    if len({candidate.asset_version_id for candidate in ranked_candidates}) != len(
        ranked_candidates
    ):
        raise ValueError("bounded retrieval candidates must contain unique Asset Versions")
    required_count = sum(candidate.required_brand_member for candidate in ranked_candidates)
    if required_count > limit:
        raise ValueError("required Brand Profile members exceed the candidate limit")
    optional_remaining = limit - required_count
    result: list[RetrievalCandidate] = []
    for candidate in ranked_candidates:
        if candidate.required_brand_member:
            result.append(candidate)
        elif optional_remaining:
            result.append(candidate)
            optional_remaining -= 1
    return tuple(result)
