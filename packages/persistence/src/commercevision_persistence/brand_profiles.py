"""MySQL adapters for Brand Profile identity, publication, and Asset authority."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import MappingProxyType, TracebackType

from commercevision_application.brand_profile_ports import (
    BrandProfileAssetAuthoritySnapshot,
    BrandProfileCurrentAssetSnapshot,
    BrandProfileCurrentAssetSnapshotBatch,
    BrandProfileInvalidationCandidate,
)
from commercevision_domain import (
    Asset,
    BrandColor,
    BrandProfile,
    BrandProfileDraft,
    BrandProfileMemberRole,
    BrandProfileMemberSelection,
    BrandProfilePublishedMember,
    BrandProfileState,
    BrandProfileVersion,
    BrandRule,
    BrandRuleScope,
    ConcurrencyError,
    RightsRecord,
)
from sqlalchemy import and_, literal_column, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .assets import (
    _asset_from_model,
    _asset_version_from_model,
    _rights_record_from_model,
)
from .brand_profile_models import (
    BrandProfileMemberModel,
    BrandProfileModel,
    BrandProfileVersionModel,
)
from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import (
    classify_database_error,
    execute_with_integrity_classification,
    flush_with_integrity_classification,
)
from .models import (
    AssetModel,
    AssetVersionModel,
    RightsRecordModel,
    RightsRecordProviderModel,
    RightsRecordUseModel,
)
from .repositories import AuditRepository, IdempotencyRepository, OutboxRepository


def _canonical_json_sha256(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _draft_from_data(data: dict[str, object]) -> BrandProfileDraft:
    if data.get("schema_version") != "brand-profile.v1":
        raise RuntimeError("unsupported persisted Brand Profile draft schema")
    try:
        rules_data = data["rules"]
        colors_data = data["approved_colors"]
        selected_data = data["selected_assets"]
        if not isinstance(rules_data, list):
            raise TypeError("rules must be a list")
        if not isinstance(colors_data, list):
            raise TypeError("approved_colors must be a list")
        if not isinstance(selected_data, list):
            raise TypeError("selected_assets must be a list")
        rules = tuple(
            BrandRule(
                code=str(item["code"]),
                scope=BrandRuleScope(str(item["scope"])),
                instruction=str(item["instruction"]),
            )
            for item in rules_data
            if isinstance(item, dict)
        )
        colors = tuple(
            BrandColor(name=str(item["name"]), value=str(item["value"]))
            for item in colors_data
            if isinstance(item, dict)
        )
        selected_assets = tuple(
            BrandProfileMemberSelection(
                asset_version_id=str(item["asset_version_id"]),
                role=BrandProfileMemberRole(str(item["role"])),
            )
            for item in selected_data
            if isinstance(item, dict)
        )
        if len(rules) != len(rules_data):
            raise TypeError("rules contains a non-object")
        if len(colors) != len(colors_data):
            raise TypeError("approved_colors contains a non-object")
        if len(selected_assets) != len(selected_data):
            raise TypeError("selected_assets contains a non-object")
        return BrandProfileDraft(
            rules=rules,
            approved_colors=colors,
            required_marks=tuple(str(value) for value in data["required_marks"]),
            prohibited_elements=tuple(str(value) for value in data["prohibited_elements"]),
            tone_constraints=tuple(str(value) for value in data["tone_constraints"]),
            copy_constraints=tuple(str(value) for value in data["copy_constraints"]),
            purpose=str(data["purpose"]),
            provider=str(data["provider"]),
            requires_derivative=data["requires_derivative"],
            selected_assets=selected_assets,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("persisted Brand Profile draft is invalid") from exc


def _profile_from_model(model: BrandProfileModel) -> BrandProfile:
    if _canonical_json_sha256(model.draft_json) != model.draft_sha256:
        raise RuntimeError("persisted Brand Profile draft checksum is inconsistent")
    return BrandProfile(
        id=model.id,
        workspace_id=model.workspace_id,
        brand=model.brand,
        profile_key=model.profile_key,
        state=BrandProfileState(model.state),
        draft=_draft_from_data(model.draft_json),
        current_version_id=model.current_version_id,
        current_version_number=model.current_version_number,
        version=model.version,
        stale_at=model.stale_at,
        created_by=model.created_by,
        created_at=model.created_at,
        updated_by=model.updated_by,
        updated_at=model.updated_at,
    )


def _profile_to_model(profile: BrandProfile) -> BrandProfileModel:
    draft_data = profile.draft.to_canonical_data()
    return BrandProfileModel(
        id=profile.id,
        workspace_id=profile.workspace_id,
        brand=profile.brand,
        profile_key=profile.profile_key,
        state=profile.state.value,
        draft_json=draft_data,
        draft_sha256=_canonical_json_sha256(draft_data),
        current_version_id=profile.current_version_id,
        current_version_number=profile.current_version_number,
        version=profile.version,
        stale_at=profile.stale_at,
        created_by=profile.created_by,
        created_at=profile.created_at,
        updated_by=profile.updated_by,
        updated_at=profile.updated_at,
    )


def _published_member_from_model(
    model: BrandProfileMemberModel,
) -> BrandProfilePublishedMember:
    return BrandProfilePublishedMember(
        ordinal=model.ordinal,
        asset_id=model.asset_id,
        asset_version_id=model.asset_version_id,
        role=BrandProfileMemberRole(model.role),
        rights_record_id=model.rights_record_id,
        rights_record_version=model.rights_record_version,
    )


def _publication_from_models(
    model: BrandProfileVersionModel,
    members: tuple[BrandProfileMemberModel, ...],
) -> BrandProfileVersion:
    draft_data = model.content_json.get("draft")
    if not isinstance(draft_data, dict):
        raise RuntimeError("persisted Brand Profile publication content is invalid")
    draft = _draft_from_data(draft_data)
    if (
        model.purpose != draft.purpose
        or model.provider != draft.provider
        or model.requires_derivative != draft.requires_derivative
    ):
        raise RuntimeError("persisted Brand Profile publication authority is inconsistent")
    return BrandProfileVersion(
        id=model.id,
        workspace_id=model.workspace_id,
        profile_id=model.profile_id,
        version_number=model.version_number,
        draft=draft,
        members=tuple(_published_member_from_model(member) for member in members),
        content_sha256=model.content_sha256,
        published_by=model.published_by,
        published_at=model.published_at,
    )


class BrandProfileIdentityRepository:
    """Mutable identity/head adapter with explicit optimistic concurrency."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, profile: BrandProfile) -> None:
        self._session.add(_profile_to_model(profile))

    def get(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        for_update: bool = False,
    ) -> BrandProfile | None:
        statement = select(BrandProfileModel).where(
            BrandProfileModel.workspace_id == workspace_id,
            BrandProfileModel.id == profile_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        return _profile_from_model(model) if model is not None else None

    def get_by_key(
        self,
        *,
        workspace_id: str,
        brand: str,
        profile_key: str,
        for_update: bool = False,
    ) -> BrandProfile | None:
        statement = select(BrandProfileModel).where(
            BrandProfileModel.workspace_id == workspace_id,
            BrandProfileModel.brand == brand,
            BrandProfileModel.profile_key == profile_key,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        return _profile_from_model(model) if model is not None else None

    def save(self, profile: BrandProfile, *, expected_version: int) -> None:
        if profile.version != expected_version + 1:
            raise ValueError("Brand Profile save requires exactly one aggregate transition")
        draft_data = profile.draft.to_canonical_data()
        result = execute_with_integrity_classification(
            self._session,
            update(BrandProfileModel)
            .where(
                BrandProfileModel.workspace_id == profile.workspace_id,
                BrandProfileModel.id == profile.id,
                BrandProfileModel.version == expected_version,
            )
            .values(
                state=profile.state.value,
                draft_json=draft_data,
                draft_sha256=_canonical_json_sha256(draft_data),
                current_version_id=profile.current_version_id,
                current_version_number=profile.current_version_number,
                version=profile.version,
                stale_at=profile.stale_at,
                updated_by=profile.updated_by,
                updated_at=profile.updated_at,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"Brand Profile {profile.id} was concurrently modified")

    def list(
        self,
        *,
        workspace_id: str,
        brand: str | None,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[BrandProfile, ...]:
        if limit < 1 or limit > 101:
            raise ValueError("Brand Profile list limit must be between 1 and 101")
        statement = select(BrandProfileModel).where(BrandProfileModel.workspace_id == workspace_id)
        if brand is not None:
            statement = statement.where(BrandProfileModel.brand == brand)
        if cursor is not None:
            created_at, profile_id = cursor
            statement = statement.where(
                or_(
                    BrandProfileModel.created_at < created_at,
                    and_(
                        BrandProfileModel.created_at == created_at,
                        BrandProfileModel.id < profile_id,
                    ),
                )
            )
        models = self._session.scalars(
            statement.order_by(
                BrandProfileModel.created_at.desc(),
                BrandProfileModel.id.desc(),
            ).limit(limit)
        )
        return tuple(_profile_from_model(model) for model in models)

    def lock_current_profiles_referencing_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> tuple[BrandProfileInvalidationCandidate, ...]:
        profile_models = tuple(
            self._session.scalars(
                select(BrandProfileModel)
                .join(
                    BrandProfileMemberModel,
                    and_(
                        BrandProfileMemberModel.workspace_id == BrandProfileModel.workspace_id,
                        BrandProfileMemberModel.profile_id == BrandProfileModel.id,
                        BrandProfileMemberModel.profile_version_id
                        == BrandProfileModel.current_version_id,
                    ),
                )
                .where(
                    BrandProfileModel.workspace_id == workspace_id,
                    BrandProfileModel.state == BrandProfileState.ACTIVE.value,
                    BrandProfileMemberModel.asset_id == asset_id,
                )
                .order_by(BrandProfileModel.id)
                .with_for_update()
            ).unique()
        )
        if not profile_models:
            return ()
        publication_ids = tuple(
            model.current_version_id
            for model in profile_models
            if model.current_version_id is not None
        )
        publication_models = tuple(
            self._session.scalars(
                select(BrandProfileVersionModel).where(
                    BrandProfileVersionModel.workspace_id == workspace_id,
                    BrandProfileVersionModel.id.in_(publication_ids),
                )
            )
        )
        publication_by_id = {model.id: model for model in publication_models}
        members_by_version: dict[str, list[BrandProfileMemberModel]] = {}
        for member in self._session.scalars(
            select(BrandProfileMemberModel)
            .where(
                BrandProfileMemberModel.workspace_id == workspace_id,
                BrandProfileMemberModel.profile_version_id.in_(publication_ids),
            )
            .order_by(
                BrandProfileMemberModel.profile_version_id,
                BrandProfileMemberModel.ordinal,
            )
        ):
            members_by_version.setdefault(member.profile_version_id, []).append(member)
        candidates: list[BrandProfileInvalidationCandidate] = []
        for profile_model in profile_models:
            publication_id = profile_model.current_version_id
            publication_model = (
                publication_by_id.get(publication_id) if publication_id is not None else None
            )
            if publication_model is None:
                raise RuntimeError("ACTIVE Brand Profile head has no current publication")
            candidates.append(
                BrandProfileInvalidationCandidate(
                    profile=_profile_from_model(profile_model),
                    publication=_publication_from_models(
                        publication_model,
                        tuple(members_by_version.get(publication_model.id, ())),
                    ),
                )
            )
        return tuple(candidates)


class BrandProfilePublicationRepository:
    """Append-only publication adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, version: BrandProfileVersion) -> None:
        version_model = BrandProfileVersionModel(
            id=version.id,
            workspace_id=version.workspace_id,
            profile_id=version.profile_id,
            version_number=version.version_number,
            content_json={"draft": version.draft.to_canonical_data()},
            content_sha256=version.content_sha256,
            purpose=version.draft.purpose,
            provider=version.draft.provider,
            requires_derivative=version.draft.requires_derivative,
            published_by=version.published_by,
            published_at=version.published_at,
        )
        self._session.add(version_model)
        # No ORM relationship is exposed at this persistence seam. Flush the
        # immutable parent explicitly so MySQL can enforce the composite member FK.
        flush_with_integrity_classification(self._session)
        self._session.add_all(
            [
                BrandProfileMemberModel(
                    workspace_id=version.workspace_id,
                    profile_id=version.profile_id,
                    profile_version_id=version.id,
                    profile_version_number=version.version_number,
                    ordinal=member.ordinal,
                    asset_id=member.asset_id,
                    asset_version_id=member.asset_version_id,
                    role=member.role.value,
                    rights_record_id=member.rights_record_id,
                    rights_record_version=member.rights_record_version,
                )
                for member in version.members
            ]
        )
        flush_with_integrity_classification(self._session)

    def get_version(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version_number: int,
    ) -> BrandProfileVersion | None:
        model = self._session.scalar(
            select(BrandProfileVersionModel).where(
                BrandProfileVersionModel.workspace_id == workspace_id,
                BrandProfileVersionModel.profile_id == profile_id,
                BrandProfileVersionModel.version_number == version_number,
            )
        )
        if model is None:
            return None
        return self._load_publication(model)

    def list_versions(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        cursor: int | None,
        limit: int,
    ) -> tuple[BrandProfileVersion, ...]:
        if limit < 1 or limit > 101:
            raise ValueError("Brand Profile history limit must be between 1 and 101")
        statement = select(BrandProfileVersionModel).where(
            BrandProfileVersionModel.workspace_id == workspace_id,
            BrandProfileVersionModel.profile_id == profile_id,
        )
        if cursor is not None:
            statement = statement.where(BrandProfileVersionModel.version_number < cursor)
        models = tuple(
            self._session.scalars(
                statement.order_by(BrandProfileVersionModel.version_number.desc()).limit(limit)
            )
        )
        if not models:
            return ()
        members_by_version: dict[str, list[BrandProfileMemberModel]] = {}
        for member in self._session.scalars(
            select(BrandProfileMemberModel)
            .where(
                BrandProfileMemberModel.workspace_id == workspace_id,
                BrandProfileMemberModel.profile_id == profile_id,
                BrandProfileMemberModel.profile_version_id.in_(tuple(model.id for model in models)),
            )
            .order_by(
                BrandProfileMemberModel.profile_version_number.desc(),
                BrandProfileMemberModel.ordinal,
            )
        ):
            members_by_version.setdefault(member.profile_version_id, []).append(member)
        return tuple(
            _publication_from_models(
                model,
                tuple(members_by_version.get(model.id, ())),
            )
            for model in models
        )

    def _load_publication(
        self,
        model: BrandProfileVersionModel,
    ) -> BrandProfileVersion:
        members = tuple(
            self._session.scalars(
                select(BrandProfileMemberModel)
                .where(
                    BrandProfileMemberModel.workspace_id == model.workspace_id,
                    BrandProfileMemberModel.profile_id == model.profile_id,
                    BrandProfileMemberModel.profile_version_id == model.id,
                    BrandProfileMemberModel.profile_version_number == model.version_number,
                )
                .order_by(BrandProfileMemberModel.ordinal)
            )
        )
        return _publication_from_models(model, members)


class BrandProfileAssetAuthorityRepository:
    """Serializes publication with Asset/Rights changes and serves current facts."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_for_publication(
        self,
        *,
        workspace_id: str,
        selected_version_ids: tuple[str, ...],
    ) -> MappingProxyType[str, BrandProfileAssetAuthoritySnapshot]:
        if len(set(selected_version_ids)) != len(selected_version_ids):
            raise ValueError("selected Asset Version ids must be unique")
        if not selected_version_ids:
            return MappingProxyType({})
        version_models = tuple(
            self._session.scalars(
                select(AssetVersionModel)
                .where(
                    AssetVersionModel.workspace_id == workspace_id,
                    AssetVersionModel.id.in_(selected_version_ids),
                )
                .order_by(AssetVersionModel.asset_id, AssetVersionModel.id)
            )
        )
        version_by_id = {model.id: model for model in version_models}
        asset_ids = sorted({model.asset_id for model in version_models})
        asset_models = tuple(
            self._session.scalars(
                select(AssetModel)
                .where(
                    AssetModel.workspace_id == workspace_id,
                    AssetModel.id.in_(asset_ids),
                )
                .order_by(AssetModel.id)
                .with_for_update()
            )
        )
        asset_by_id = {model.id: model for model in asset_models}
        rights_by_id = self._current_rights_by_id(
            workspace_id=workspace_id,
            asset_models=asset_models,
        )
        snapshots: dict[str, BrandProfileAssetAuthoritySnapshot] = {}
        for version_id in selected_version_ids:
            version_model = version_by_id.get(version_id)
            if version_model is None:
                continue
            asset_model = asset_by_id.get(version_model.asset_id)
            if asset_model is None:
                continue
            snapshots[version_id] = BrandProfileAssetAuthoritySnapshot(
                asset=_asset_from_model(asset_model),
                asset_version=_asset_version_from_model(version_model),
                current_rights_record=rights_by_id.get(asset_model.current_rights_record_id),
            )
        return MappingProxyType(snapshots)

    def current_snapshots(
        self,
        *,
        workspace_id: str,
        asset_ids: tuple[str, ...],
    ) -> BrandProfileCurrentAssetSnapshotBatch:
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("Asset ids must be unique")
        if not asset_ids:
            return BrandProfileCurrentAssetSnapshotBatch(
                decided_at=self._database_now(),
                snapshots=MappingProxyType({}),
            )
        rows = tuple(
            self._session.execute(
                select(
                    AssetModel,
                    RightsRecordModel,
                )
                .outerjoin(
                    RightsRecordModel,
                    and_(
                        RightsRecordModel.workspace_id == AssetModel.workspace_id,
                        RightsRecordModel.id == AssetModel.current_rights_record_id,
                        RightsRecordModel.asset_id == AssetModel.id,
                    ),
                )
                .where(
                    AssetModel.workspace_id == workspace_id,
                    AssetModel.id.in_(asset_ids),
                )
                .order_by(AssetModel.id)
                .with_for_update(read=True)
            )
        )
        rights_models = tuple(
            rights_model
            for _, rights_model in rows
            if rights_model is not None and rights_model.permissions_sealed_at is not None
        )
        rights_by_id = self._rights_from_models(rights_models)
        # MySQL fixes statement time before a locking read starts waiting. Sample time only
        # after the Asset/current-Rights rows and their immutable permission children have
        # been read under the held shared locks, so expiry is decided at the actual snapshot.
        decided_at = self._database_now()
        return BrandProfileCurrentAssetSnapshotBatch(
            decided_at=decided_at,
            snapshots=MappingProxyType(
                {
                    asset_model.id: BrandProfileCurrentAssetSnapshot(
                        asset=_asset_from_model(asset_model),
                        current_rights_record=(
                            rights_by_id.get(rights_model.id) if rights_model is not None else None
                        ),
                    )
                    for asset_model, rights_model in rows
                }
            ),
        )

    def lock_current_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> BrandProfileCurrentAssetSnapshot | None:
        row = self._session.execute(
            select(AssetModel, RightsRecordModel)
            .outerjoin(
                RightsRecordModel,
                and_(
                    RightsRecordModel.workspace_id == AssetModel.workspace_id,
                    RightsRecordModel.id == AssetModel.current_rights_record_id,
                    RightsRecordModel.asset_id == AssetModel.id,
                ),
            )
            .where(
                AssetModel.workspace_id == workspace_id,
                AssetModel.id == asset_id,
            )
            .with_for_update()
        ).one_or_none()
        if row is None:
            return None
        asset_model, rights_model = row
        eligible_rights_models = (
            (rights_model,)
            if rights_model is not None and rights_model.permissions_sealed_at is not None
            else ()
        )
        rights_by_id = self._rights_from_models(eligible_rights_models)
        return BrandProfileCurrentAssetSnapshot(
            asset=_asset_from_model(asset_model),
            current_rights_record=(
                rights_by_id.get(rights_model.id) if rights_model is not None else None
            ),
        )

    def lock_asset_deletion_lineage(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> Asset | None:
        """Lock the retained Asset aggregate used as deletion lineage authority."""

        model = self._session.scalar(
            select(AssetModel)
            .where(
                AssetModel.workspace_id == workspace_id,
                AssetModel.id == asset_id,
            )
            .with_for_update()
        )
        return _asset_from_model(model) if model is not None else None

    def _current_rights_by_id(
        self,
        *,
        workspace_id: str,
        asset_models: tuple[AssetModel, ...],
    ) -> dict[str, RightsRecord]:
        rights_record_ids = sorted(
            {
                model.current_rights_record_id
                for model in asset_models
                if model.current_rights_record_id is not None
            }
        )
        if not rights_record_ids:
            return {}
        rights_models = tuple(
            self._session.scalars(
                select(RightsRecordModel).where(
                    RightsRecordModel.workspace_id == workspace_id,
                    RightsRecordModel.id.in_(rights_record_ids),
                    RightsRecordModel.permissions_sealed_at.is_not(None),
                )
            )
        )
        return self._rights_from_models(rights_models)

    def _rights_from_models(
        self,
        rights_models: tuple[RightsRecordModel, ...],
    ) -> dict[str, RightsRecord]:
        record_ids = [model.id for model in rights_models]
        uses: dict[str, set[str]] = {}
        providers: dict[str, set[str]] = {}
        if record_ids:
            ownership_scope = or_(
                *(
                    and_(
                        RightsRecordUseModel.workspace_id == model.workspace_id,
                        RightsRecordUseModel.asset_id == model.asset_id,
                        RightsRecordUseModel.rights_record_id == model.id,
                    )
                    for model in rights_models
                )
            )
            for record_id, allowed_use in self._session.execute(
                select(
                    RightsRecordUseModel.rights_record_id,
                    RightsRecordUseModel.allowed_use,
                ).where(ownership_scope)
            ):
                uses.setdefault(record_id, set()).add(allowed_use)
            provider_ownership_scope = or_(
                *(
                    and_(
                        RightsRecordProviderModel.workspace_id == model.workspace_id,
                        RightsRecordProviderModel.asset_id == model.asset_id,
                        RightsRecordProviderModel.rights_record_id == model.id,
                    )
                    for model in rights_models
                )
            )
            for record_id, allowed_provider in self._session.execute(
                select(
                    RightsRecordProviderModel.rights_record_id,
                    RightsRecordProviderModel.allowed_provider,
                ).where(provider_ownership_scope)
            ):
                providers.setdefault(record_id, set()).add(allowed_provider)
        return {
            model.id: _rights_record_from_model(
                model,
                allowed_uses=frozenset(uses.get(model.id, set())),
                allowed_providers=frozenset(providers.get(model.id, set())),
            )
            for model in rights_models
        }

    def _database_now(self) -> datetime:
        return self._coerce_database_now(
            self._session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        )

    @staticmethod
    def _coerce_database_now(value: object) -> datetime:
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlAlchemyBrandProfileUnitOfWork:
    """Short-lived transaction composing the three Brand Profile ports."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyBrandProfileUnitOfWork:
        self._session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        self.brand_profiles = BrandProfileIdentityRepository(self._session)
        self.brand_profile_publications = BrandProfilePublicationRepository(self._session)
        self.brand_profile_assets = BrandProfileAssetAuthorityRepository(self._session)
        self.idempotency = IdempotencyRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        self.audit = AuditRepository(self._session)
        return self

    def database_now(self) -> datetime:
        if self._session is None:
            raise RuntimeError("Brand Profile unit of work is not active")
        value = self._session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Brand Profile unit of work is not active")
        try:
            self._session.commit()
        except DBAPIError as exc:
            self._session.rollback()
            classified = classify_database_error(exc)
            if classified is None:
                raise
            raise classified from exc
        self._committed = True

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
        finally:
            if self._session is not None:
                self._session.close()
            if self._depth_token is not None:
                exit_unit_of_work(self._depth_token)
