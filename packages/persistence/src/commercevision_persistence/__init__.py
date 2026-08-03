"""MySQL infrastructure shared by CommerceVision services."""

from .assets import SqlAlchemyAssetUnitOfWork
from .brand_profiles import SqlAlchemyBrandProfileUnitOfWork
from .catalog import SqlAlchemyCatalogUnitOfWork
from .checkpointer import MySQLCheckpointSaver
from .database import (
    Database,
    create_database,
    create_readiness_database,
    is_unit_of_work_active,
)
from .indexing import MySqlExactImageReference, MySqlIndexingAuthority
from .indexing_requests import (
    ImageIndexNotApplicable,
    ImageIndexRequestResult,
    MySqlImageIndexRequestService,
    MySqlIndexRequestService,
    MySqlProductFusedIndexRequestService,
    ProductFusedIndexNotApplicable,
    ProductFusedIndexRequestResult,
)
from .indexing_status import SqlAlchemyImageIndexStatusQueries
from .operations import SqlAlchemyOperationUnitOfWork
from .operator import SqlAlchemyOperatorUnitOfWork
from .product_brief_views import SqlAlchemyProductBriefViewQueries
from .product_briefs import (
    SqlAlchemyProductBriefUnitOfWork,
    SqlAlchemyProviderArtifactTargetReadinessQuery,
)
from .product_search import MySqlProductLexicalSearch, ProductLexicalHit
from .unit_of_work import SqlAlchemyUnitOfWork

__all__ = [
    "Database",
    "MySQLCheckpointSaver",
    "MySqlIndexingAuthority",
    "MySqlImageIndexRequestService",
    "MySqlIndexRequestService",
    "MySqlProductFusedIndexRequestService",
    "ImageIndexRequestResult",
    "ImageIndexNotApplicable",
    "ProductFusedIndexRequestResult",
    "ProductFusedIndexNotApplicable",
    "MySqlProductLexicalSearch",
    "ProductLexicalHit",
    "SqlAlchemyImageIndexStatusQueries",
    "MySqlExactImageReference",
    "SqlAlchemyAssetUnitOfWork",
    "SqlAlchemyBrandProfileUnitOfWork",
    "SqlAlchemyCatalogUnitOfWork",
    "SqlAlchemyOperationUnitOfWork",
    "SqlAlchemyOperatorUnitOfWork",
    "SqlAlchemyProductBriefUnitOfWork",
    "SqlAlchemyProductBriefViewQueries",
    "SqlAlchemyProviderArtifactTargetReadinessQuery",
    "SqlAlchemyUnitOfWork",
    "create_database",
    "create_readiness_database",
    "is_unit_of_work_active",
]
