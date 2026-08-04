"""Strict, read-only audit of Phase 2 release evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

_SCHEMA_VERSION = "commercevision.phase2-release-acceptance.v1"
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_EVIDENCE_BYTES = 8 * 1_048_576
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_REQUIRED_REQUIREMENTS = (
    "browser-e2e",
    "fault-injection",
    "recovery-convergence",
    "migration-paths",
    "compose-health",
    "quality-security-supply-chain",
    "public-demo-isolation",
    "metadata-phase",
    "documentation-alignment",
    "retrieval-safety",
)
_REQUIRED_FAULTS = (
    "minio",
    "milvus",
    "rabbitmq",
    "clamav",
    "content-safety",
    "vision",
    "embedding",
    "reranker",
    "worker",
    "rebuild",
)
_REQUIRED_INVARIANTS = (
    "unique-logical-operation",
    "unique-vector",
    "zero-unauthorized-return",
    "no-retention-extension",
    "eventual-convergence",
    "incremental-indexing",
    "rebuild-recovery",
    "product-brief-restart",
)
_REQUIRED_CI_GATES = (
    "python",
    "web",
    "openapi",
    "mcp",
    "providers",
    "real-infrastructure",
    "e2e",
    "evaluation",
    "security",
    "dependencies",
    "containers",
    "licenses",
    "sbom",
)
_QUOTA_BOUNDS = MappingProxyType(
    {
        "requests_per_minute": (1, 600),
        "concurrent_operations": (1, 32),
        "provider_calls_per_day": (1, 10_000),
        "storage_bytes": (1, 10 * 1024 * 1024 * 1024),
    }
)


@dataclass(frozen=True, slots=True)
class ReleaseEvidence:
    """One immutable source reference included in a release audit."""

    path: str
    anchor: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Phase2ReleaseReport:
    """Aggregate release proof safe to retain as a CI artifact."""

    schema_version: str
    release_id: str
    phase: str
    manifest_sha256: str
    passed: bool
    requirement_ids: tuple[str, ...]
    fault_components: tuple[str, ...]
    recovery_invariant_ids: tuple[str, ...]
    ci_gate_ids: tuple[str, ...]
    public_demo_workspace_ids: tuple[str, ...]
    public_demo_bucket_names: tuple[str, ...]
    public_demo_dataset_ids: tuple[str, ...]
    evidence: tuple[ReleaseEvidence, ...]


def _reject_duplicate_keys(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError("release manifest contains duplicate JSON keys")
        value[key] = item
    return value


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    size = path.stat().st_size
    if size > _MAX_MANIFEST_BYTES:
        raise ValueError("release manifest exceeds the size limit")
    payload = path.read_bytes()
    try:
        value = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("release manifest must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError("release manifest root must be an object")
    return value, payload


def _exact_keys(value: dict[str, Any], expected: set[str], *, field: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} must contain exactly {sorted(expected)}")


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _array(value: object, *, field: str, allow_empty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "an array" if allow_empty else "a non-empty array"
        raise ValueError(f"{field} must be {qualifier}")
    return value


def _string(value: object, *, field: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{field} must be a bounded non-empty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} must not contain control characters")
    return value


def _token(value: object, *, field: str) -> str:
    item = _string(value, field=field)
    if _TOKEN.fullmatch(item) is None:
        raise ValueError(f"{field} must be a canonical token")
    return item


def _unique_tokens(value: object, *, field: str, allow_empty: bool = False) -> tuple[str, ...]:
    items = tuple(
        _token(item, field=f"{field} item")
        for item in _array(value, field=field, allow_empty=allow_empty)
    )
    if len(set(items)) != len(items):
        raise ValueError(f"{field} must contain unique values")
    return items


def _safe_path(root: Path, raw_path: object, *, field: str) -> tuple[str, Path]:
    relative = _string(raw_path, field=field, maximum=512)
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or candidate == Path("."):
        raise ValueError(f"{field} must remain inside the repository")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{field} must remain inside the repository")
    if not resolved.is_file():
        raise ValueError(f"{field} must reference an existing file")
    return candidate.as_posix(), resolved


def _manifest_path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        _, resolved = _safe_path(root, str(candidate), field="release manifest path")
        return resolved
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("release manifest path must reference a file inside the repository")
    return resolved


def _required_entries(
    value: object,
    *,
    list_field: str,
    id_field: str,
    required: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw_entry in enumerate(_array(value, field=list_field)):
        entry = _object(raw_entry, field=f"{list_field}[{index}]")
        identifier = _token(entry.get(id_field), field=f"{list_field}[{index}].{id_field}")
        if identifier in by_id:
            raise ValueError(f"{list_field} contains duplicate {id_field}")
        by_id[identifier] = entry
    if set(by_id) != set(required):
        raise ValueError(f"{list_field} must cover the exact Phase 2 set")
    return tuple(by_id[identifier] for identifier in required)


def _load_evidence(
    value: object,
    *,
    root: Path,
    field: str,
    cache: dict[Path, tuple[str, str]],
) -> ReleaseEvidence:
    item = _object(value, field=field)
    _exact_keys(item, {"path", "anchor"}, field=field)
    relative, resolved = _safe_path(root, item["path"], field=f"{field} path")
    anchor = _string(item["anchor"], field=f"{field} anchor", maximum=512)
    cached = cache.get(resolved)
    if cached is None:
        if resolved.stat().st_size > _MAX_EVIDENCE_BYTES:
            raise ValueError(f"{field} file exceeds the evidence size limit")
        payload = resolved.read_bytes()
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"{field} file must be UTF-8 text") from error
        cached = (content, hashlib.sha256(payload).hexdigest())
        cache[resolved] = cached
    content, digest = cached
    if anchor not in content:
        raise ValueError(f"{field} anchor is absent from its evidence file")
    return ReleaseEvidence(path=relative, anchor=anchor, sha256=digest)


def _validate_boundaries(manifest: dict[str, Any], root: Path) -> tuple[tuple[str, ...], ...]:
    private = _object(manifest["private_boundaries"], field="private_boundaries")
    _exact_keys(
        private,
        {"workspace_ids", "bucket_names", "credential_scopes", "dataset_paths"},
        field="private_boundaries",
    )
    demo = _object(manifest["public_demo"], field="public_demo")
    _exact_keys(
        demo,
        {
            "workspace_ids",
            "admin_workspace_ids",
            "bucket_names",
            "object_prefix",
            "credential_scopes",
            "quotas",
            "datasets",
        },
        field="public_demo",
    )
    private_sets = {
        name: set(_unique_tokens(private[name], field=f"private_boundaries.{name}"))
        for name in ("workspace_ids", "bucket_names", "credential_scopes", "dataset_paths")
    }
    workspaces = _unique_tokens(demo["workspace_ids"], field="public_demo.workspace_ids")
    admins = _unique_tokens(
        demo["admin_workspace_ids"],
        field="public_demo.admin_workspace_ids",
        allow_empty=True,
    )
    if admins:
        raise ValueError("public-demo administrator workspaces must be empty")
    buckets = _unique_tokens(demo["bucket_names"], field="public_demo.bucket_names")
    if len(buckets) != 4 or any(not value.startswith("public-demo-") for value in buckets):
        raise ValueError("public-demo must use four dedicated public-demo buckets")
    credential_scopes = _unique_tokens(
        demo["credential_scopes"], field="public_demo.credential_scopes"
    )
    if any(not value.startswith("secret://") for value in credential_scopes):
        raise ValueError("public-demo credentials must be opaque secret references")
    prefix = _string(demo["object_prefix"], field="public_demo.object_prefix")
    if not prefix.endswith("/") or prefix.startswith("/") or ".." in Path(prefix).parts:
        raise ValueError("public-demo object prefix must be a safe relative prefix")

    public_sets = {
        "workspace_ids": set(workspaces),
        "bucket_names": set(buckets),
        "credential_scopes": set(credential_scopes),
    }
    for name, values in public_sets.items():
        if values & private_sets[name]:
            raise ValueError(f"public-demo {name} overlaps private configuration")

    quotas = _object(demo["quotas"], field="public_demo.quotas")
    _exact_keys(quotas, set(_QUOTA_BOUNDS), field="public_demo.quotas")
    for name, (minimum, maximum) in _QUOTA_BOUNDS.items():
        value = quotas[name]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"public-demo quota {name} is outside its safe bound")

    dataset_ids: list[str] = []
    dataset_paths: set[str] = set()
    for index, raw_dataset in enumerate(_array(demo["datasets"], field="public_demo.datasets")):
        dataset = _object(raw_dataset, field=f"public_demo.datasets[{index}]")
        _exact_keys(
            dataset,
            {"dataset_id", "version", "license", "path", "public_demo_allowed"},
            field=f"public_demo.datasets[{index}]",
        )
        dataset_ids.append(
            _token(dataset["dataset_id"], field=f"public_demo.datasets[{index}].dataset_id")
        )
        _token(dataset["version"], field=f"public_demo.datasets[{index}].version")
        license_expression = _token(
            dataset["license"], field=f"public_demo.datasets[{index}].license"
        )
        if license_expression.lower() in {"unknown", "unlicensed"}:
            raise ValueError("public-demo datasets require an explicit license")
        if dataset["public_demo_allowed"] is not True:
            raise ValueError("public-demo datasets must be explicitly authorized")
        relative, _ = _safe_path(
            root,
            dataset["path"],
            field=f"public_demo.datasets[{index}].path",
        )
        dataset_paths.add(relative)
    if len(set(dataset_ids)) != len(dataset_ids):
        raise ValueError("public-demo datasets must have unique identities")
    if dataset_paths & private_sets["dataset_paths"]:
        raise ValueError("public-demo dataset_paths overlaps private configuration")
    return workspaces, buckets, tuple(dataset_ids)


def audit_phase2_release(
    manifest_path: str | Path,
    *,
    repository_root: str | Path,
) -> Phase2ReleaseReport:
    """Validate fixed Phase 2 evidence without executing manifest-controlled content."""

    root = Path(repository_root).resolve()
    if not root.is_dir():
        raise ValueError("repository root must be an existing directory")
    manifest = _manifest_path(root, manifest_path)
    value, payload = _read_json(manifest)
    _exact_keys(
        value,
        {
            "schema_version",
            "release_id",
            "phase",
            "private_boundaries",
            "public_demo",
            "requirements",
            "fault_injection",
            "recovery_invariants",
            "ci_gates",
        },
        field="release manifest",
    )
    if value["schema_version"] != _SCHEMA_VERSION or value["phase"] != "phase-2":
        raise ValueError("release manifest schema and phase must identify Phase 2")
    release_id = _token(value["release_id"], field="release_id")
    workspaces, buckets, dataset_ids = _validate_boundaries(value, root)
    requirements = _required_entries(
        value["requirements"],
        list_field="requirements",
        id_field="id",
        required=_REQUIRED_REQUIREMENTS,
    )
    faults = _required_entries(
        value["fault_injection"],
        list_field="fault_injection",
        id_field="component",
        required=_REQUIRED_FAULTS,
    )
    invariants = _required_entries(
        value["recovery_invariants"],
        list_field="recovery_invariants",
        id_field="id",
        required=_REQUIRED_INVARIANTS,
    )
    gates = _required_entries(
        value["ci_gates"],
        list_field="ci_gates",
        id_field="id",
        required=_REQUIRED_CI_GATES,
    )

    cache: dict[Path, tuple[str, str]] = {}
    evidence: list[ReleaseEvidence] = []
    for index, requirement in enumerate(requirements):
        _exact_keys(requirement, {"id", "evidence"}, field=f"requirements[{index}]")
        evidence.extend(
            _load_evidence(
                item,
                root=root,
                field=f"requirements[{index}].evidence[{evidence_index}]",
                cache=cache,
            )
            for evidence_index, item in enumerate(
                _array(requirement["evidence"], field=f"requirements[{index}].evidence")
            )
        )
    for field, entries, id_field in (
        ("fault_injection", faults, "component"),
        ("recovery_invariants", invariants, "id"),
        ("ci_gates", gates, "id"),
    ):
        for index, entry in enumerate(entries):
            _exact_keys(entry, {id_field, "evidence"}, field=f"{field}[{index}]")
            evidence.append(
                _load_evidence(
                    entry["evidence"],
                    root=root,
                    field=f"{field}[{index}].evidence",
                    cache=cache,
                )
            )

    return Phase2ReleaseReport(
        schema_version=_SCHEMA_VERSION,
        release_id=release_id,
        phase="phase-2",
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
        passed=True,
        requirement_ids=_REQUIRED_REQUIREMENTS,
        fault_components=_REQUIRED_FAULTS,
        recovery_invariant_ids=_REQUIRED_INVARIANTS,
        ci_gate_ids=_REQUIRED_CI_GATES,
        public_demo_workspace_ids=workspaces,
        public_demo_bucket_names=buckets,
        public_demo_dataset_ids=dataset_ids,
        evidence=tuple(evidence),
    )
