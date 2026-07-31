"""Brand Profile domain enumerations."""

from enum import StrEnum


class BrandProfileState(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    NEEDS_REPUBLISH = "NEEDS_REPUBLISH"
    ARCHIVED = "ARCHIVED"


class BrandRuleScope(StrEnum):
    VISUAL = "VISUAL"
    COPY = "COPY"
    COMPOSITION = "COMPOSITION"
    LEGAL = "LEGAL"
    GENERAL = "GENERAL"


class BrandProfileMemberRole(StrEnum):
    LOGO = "LOGO"
    REQUIRED_MARK = "REQUIRED_MARK"
    VISUAL_REFERENCE = "VISUAL_REFERENCE"
    PROMPT_TEMPLATE = "PROMPT_TEMPLATE"
    MODEL_CONFIGURATION = "MODEL_CONFIGURATION"
    LORA = "LORA"
