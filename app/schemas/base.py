"""Base Schemas"""
from pydantic import BaseModel as PydanticBaseModel, ConfigDict


class BaseSchema(PydanticBaseModel):
    """アプリケーション共通のベーススキーマ"""

    model_config = ConfigDict(
        from_attributes=True,
        validate_default=True,
        by_alias=True
    )