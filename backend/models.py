"""HTTP API request models."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProbeStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1, max_length=200)

    @field_validator("keyword")
    @classmethod
    def normalize_keyword(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("keyword cannot be empty")
        return normalized


class ProbeMarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=100)
    details: dict[str, Any] = Field(default_factory=dict)


class BrowserNavigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=24, max_length=2048)

    @field_validator("url")
    @classmethod
    def allow_kuaishou_web_only(cls, value: str) -> str:
        normalized = value.strip()
        parts = urlsplit(normalized)
        if parts.scheme != "https" or parts.hostname != "www.kuaishou.com":
            raise ValueError("only https://www.kuaishou.com URLs are allowed")
        return normalized


class VideoInspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str = Field(min_length=6, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    timeout_seconds: float = Field(default=15.0, ge=2.0, le=60.0)


class BrowserScrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distance: int = Field(default=1800, ge=100, le=20_000)
