"""HTTP API request models."""

from __future__ import annotations

from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class SamplingStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Literal["search", "comments"] = "search"
    collection_mode: Literal[
        "anonymous_repeat", "logged_in_full_scroll"
    ] = "anonymous_repeat"
    limit: int = Field(default=10, ge=1, le=10_000)
    loops: int = Field(default=20, ge=1, le=1_000)
    min_interval: float = Field(default=6.0, ge=0.0, le=120.0)
    max_interval: float = Field(default=12.0, ge=0.0, le=120.0)
    max_consecutive: int = Field(default=5, ge=1, le=5)
    max_attempts: int = Field(default=250, ge=1, le=100_000)
    open_browser_window: bool = True
    auto_reset_identity: bool = False
    scroll_min_interval: float = Field(default=1.5, ge=0.0, le=120.0)
    scroll_max_interval: float = Field(default=3.0, ge=0.0, le=120.0)
    max_scrolls_per_keyword: int = Field(default=300, ge=1, le=2_000)
    comment_min_interval: float = Field(default=1.5, ge=0.0, le=120.0)
    comment_max_interval: float = Field(default=3.5, ge=0.0, le=120.0)

    @model_validator(mode="after")
    def validate_interval(self) -> "SamplingStartRequest":
        if self.min_interval > self.max_interval:
            raise ValueError("min_interval must be <= max_interval")
        if self.comment_min_interval > self.comment_max_interval:
            raise ValueError(
                "comment_min_interval must be <= comment_max_interval"
            )
        if self.scroll_min_interval > self.scroll_max_interval:
            raise ValueError(
                "scroll_min_interval must be <= scroll_max_interval"
            )
        return self


class BrowserWindowModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_browser_window: bool = True


def _normalize_proxy(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    parts = urlsplit(normalized)
    if parts.scheme not in {"http", "https", "socks5", "socks5h"} or not parts.hostname:
        raise ValueError("代理地址必须是 http、https、socks5 或 socks5h URL")
    return normalized


class ProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    proxy: Optional[str] = Field(default=None, max_length=2048)
    activate: bool = True
    open_browser_window: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Profile 名称不能为空")
        return normalized

    @field_validator("proxy")
    @classmethod
    def normalize_proxy(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_proxy(value)


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    proxy: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Profile 名称不能为空")
        return normalized

    @field_validator("proxy")
    @classmethod
    def normalize_optional_proxy(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_proxy(value)


class ProfileActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_browser_window: bool = True
