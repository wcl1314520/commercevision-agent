"""ProductBrief domain enumerations."""

from enum import StrEnum


class ProductBriefCategory(StrEnum):
    BEAUTY = "BEAUTY"
    AUTOMOTIVE = "AUTOMOTIVE"


class ProductBriefState(StrEnum):
    DRAFT = "DRAFT"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    ARCHIVED = "ARCHIVED"


class ProductBriefVersionSource(StrEnum):
    MODEL = "MODEL"
    HUMAN = "HUMAN"


class ProductBriefFieldSource(StrEnum):
    MODEL = "MODEL"
    HUMAN = "HUMAN"
    PRODUCT_DATA = "PRODUCT_DATA"


class ProductBriefFieldConflict(StrEnum):
    NONE = "NONE"
    CONFLICTING = "CONFLICTING"
    RESOLVED = "RESOLVED"


class ProductBriefEvidenceKind(StrEnum):
    IMAGE_REGION = "IMAGE_REGION"
    VISIBLE_TEXT = "VISIBLE_TEXT"
    PRODUCT_DATA = "PRODUCT_DATA"
    HUMAN_NOTE = "HUMAN_NOTE"
