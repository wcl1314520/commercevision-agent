"""Bound and normalize native C2PA Reader output into safe evidence facts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

from commercevision_contracts.validation import ProvenanceEvidenceStatus


@dataclass(frozen=True, slots=True)
class C2paReaderEvidence:
    status: ProvenanceEvidenceStatus
    validation_state: str | None
    manifest_count: int
    failure_codes: tuple[str, ...]


class C2paEvidenceNormalizer:
    """Convert untrusted native reports without retaining claims or explanations."""

    def __init__(
        self,
        *,
        maximum_report_bytes: int,
        maximum_report_depth: int,
        maximum_report_nodes: int,
        maximum_manifests: int,
        maximum_status_codes: int,
    ) -> None:
        self._maximum_report_bytes = maximum_report_bytes
        self._maximum_report_depth = maximum_report_depth
        self._maximum_report_nodes = maximum_report_nodes
        self._maximum_manifests = maximum_manifests
        self._maximum_status_codes = maximum_status_codes

    def normalize(
        self,
        *,
        manifest_json: str,
        validation_state: str | None,
        validation_results: dict[str, object] | None,
    ) -> C2paReaderEvidence:
        report = self._parse_report(manifest_json)
        normalized_state = self._parse_validation_state(validation_state)
        normalized_results = self._parse_validation_results(validation_results)
        return self._map_report(
            report,
            validation_state=normalized_state,
            validation_results=normalized_results,
        )

    def _parse_report(self, report: str) -> dict[str, object]:
        if not isinstance(report, str):
            raise ValueError("C2PA report is not text")
        if len(report.encode("utf-8")) > self._maximum_report_bytes:
            raise ValueError("C2PA report exceeds byte bound")

        def reject_constant(_value: str) -> object:
            raise ValueError("C2PA report contains a non-finite number")

        def bounded_integer(value: str) -> int:
            if len(value.lstrip("-")) > 19:
                raise ValueError("C2PA report integer exceeds width bound")
            return int(value)

        def bounded_float(value: str) -> float:
            if len(value) > 64:
                raise ValueError("C2PA report float exceeds width bound")
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError("C2PA report float must be finite")
            return parsed

        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("C2PA report contains duplicate keys")
                result[key] = value
            return result

        parsed = json.loads(
            report,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=bounded_float,
            parse_int=bounded_integer,
        )
        if not isinstance(parsed, dict):
            raise ValueError("C2PA report root must be an object")
        self._validate_report_tree(parsed)
        return parsed

    def _validate_report_tree(self, report: dict[str, object]) -> None:
        nodes = 0
        stack: list[tuple[object, int]] = [(report, 1)]
        while stack:
            value, depth = stack.pop()
            nodes += 1
            if nodes > self._maximum_report_nodes:
                raise ValueError("C2PA report exceeds node bound")
            if depth > self._maximum_report_depth:
                raise ValueError("C2PA report exceeds depth bound")
            if isinstance(value, dict):
                for key, child in value.items():
                    if not isinstance(key, str) or len(key) > 256:
                        raise ValueError("C2PA report key is invalid")
                    stack.append((child, depth + 1))
            elif isinstance(value, list):
                stack.extend((child, depth + 1) for child in value)
            elif isinstance(value, str):
                if len(value) > 4096:
                    raise ValueError("C2PA report string exceeds bound")
            elif value is not None and not isinstance(value, (bool, int, float)):
                raise ValueError("C2PA report contains an unsupported value")

    @staticmethod
    def _parse_validation_state(state: str | None) -> str | None:
        if state is None:
            return None
        if not isinstance(state, str):
            raise ValueError("C2PA validation state is malformed")
        normalized = state.strip().upper()
        if normalized not in {"TRUSTED", "VALID", "INVALID"}:
            raise ValueError("C2PA validation state is malformed")
        return normalized

    def _parse_validation_results(
        self,
        results: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if results is None:
            return None
        if not isinstance(results, dict):
            raise ValueError("C2PA validation results are malformed")
        self._validate_report_tree(results)
        return results

    def _map_report(
        self,
        report: dict[str, object],
        *,
        validation_state: str | None,
        validation_results: dict[str, object] | None,
    ) -> C2paReaderEvidence:
        manifests = report.get("manifests")
        active_manifest = report.get("active_manifest")
        if active_manifest is None and manifests in ({}, None):
            return C2paReaderEvidence(
                status=ProvenanceEvidenceStatus.NOT_PRESENT,
                validation_state=None,
                manifest_count=0,
                failure_codes=(),
            )
        if (
            not isinstance(manifests, Mapping)
            or not 1 <= len(manifests) <= self._maximum_manifests
            or not isinstance(active_manifest, str)
            or not active_manifest
            or len(active_manifest) > 512
            or active_manifest not in manifests
        ):
            raise ValueError("C2PA manifest index is malformed")
        if any(not isinstance(label, str) or not label or len(label) > 512 for label in manifests):
            raise ValueError("C2PA manifest label is malformed")

        failure_codes = self._extract_failure_codes(
            report,
            validation_results=validation_results,
        )
        if failure_codes or validation_state == "INVALID":
            status = ProvenanceEvidenceStatus.CONFLICTING
            if validation_state == "INVALID" and not failure_codes:
                failure_codes = ("VALIDATION_STATE_INVALID",)
        elif validation_state == "TRUSTED":
            status = ProvenanceEvidenceStatus.VERIFIED
        else:
            status = ProvenanceEvidenceStatus.UNVERIFIED
        return C2paReaderEvidence(
            status=status,
            validation_state=validation_state,
            manifest_count=len(manifests),
            failure_codes=failure_codes,
        )

    def _extract_failure_codes(
        self,
        report: dict[str, object],
        *,
        validation_results: dict[str, object] | None,
    ) -> tuple[str, ...]:
        raw_statuses: list[object] = []
        legacy = report.get("validation_status")
        if legacy is not None:
            if not isinstance(legacy, list):
                raise ValueError("C2PA legacy validation status is malformed")
            raw_statuses.extend(legacy)

        if validation_results is not None:
            active = validation_results.get("activeManifest")
            if active is not None:
                raw_statuses.extend(self._failure_list(active))
            deltas = validation_results.get("ingredientDeltas", [])
            if not isinstance(deltas, list):
                raise ValueError("C2PA ingredient deltas are malformed")
            for delta in deltas:
                if not isinstance(delta, Mapping):
                    raise ValueError("C2PA ingredient delta is malformed")
                statuses = delta.get("validationDeltas")
                if statuses is not None:
                    raw_statuses.extend(self._failure_list(statuses))

        if len(raw_statuses) > self._maximum_status_codes:
            raise ValueError("C2PA status count exceeds bound")
        codes: set[str] = set()
        for raw_status in raw_statuses:
            if not isinstance(raw_status, Mapping):
                raise ValueError("C2PA validation status is malformed")
            code = raw_status.get("code")
            if (
                not isinstance(code, str)
                or not code
                or len(code) > 128
                or not code.isascii()
                or any(
                    not (character.isalnum() or character in {"_", "-", ".", ":"})
                    for character in code
                )
            ):
                raise ValueError("C2PA validation code is malformed")
            codes.add(code)
        return tuple(sorted(codes))

    @staticmethod
    def _failure_list(statuses: object) -> list[object]:
        if not isinstance(statuses, Mapping):
            raise ValueError("C2PA status-code groups are malformed")
        failure = statuses.get("failure", [])
        if not isinstance(failure, list):
            raise ValueError("C2PA failure statuses are malformed")
        return failure
