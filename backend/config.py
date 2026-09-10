"""Runtime configuration for the Kuaishou probe service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    profile_dir: Path
    captures_dir: Path
    headless: bool
    browser_locale: str | None
    browser_timezone: str | None
    browser_geoip: bool
    browser_width: int
    browser_height: int
    max_body_bytes: int
    max_post_data_bytes: int
    navigation_timeout_ms: int
    license_key: str | None
    release_channel: str | None
    proxy: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(
            os.environ.get("KUAISHOU_DATA_DIR", str(Path.cwd() / "runtime"))
        ).expanduser()
        proxy = os.environ.get("KUAISHOU_PROXY") or None
        return cls(
            data_dir=data_dir,
            profile_dir=data_dir / "profiles" / "kuaishou",
            captures_dir=data_dir / "captures",
            # Automated collection runs in the background so Chromium never
            # steals desktop focus. Set the variable to false only for manual
            # login/captcha diagnosis.
            headless=_env_bool("KUAISHOU_BROWSER_HEADLESS", True),
            # Native values are the safest default.  An explicit override can
            # make timezone/locale disagree with the exit IP and trigger risk
            # controls.  When a proxy is configured, geoip can align them.
            browser_locale=os.environ.get("KUAISHOU_BROWSER_LOCALE") or None,
            browser_timezone=os.environ.get("KUAISHOU_BROWSER_TIMEZONE") or None,
            browser_geoip=_env_bool("KUAISHOU_BROWSER_GEOIP", bool(proxy)),
            browser_width=_env_int("KUAISHOU_BROWSER_WIDTH", 1440, 800),
            browser_height=_env_int("KUAISHOU_BROWSER_HEIGHT", 900, 600),
            max_body_bytes=_env_int(
                "KUAISHOU_PROBE_MAX_BODY_BYTES", 25 * 1024 * 1024
            ),
            max_post_data_bytes=_env_int(
                "KUAISHOU_PROBE_MAX_POST_BYTES", 2 * 1024 * 1024
            ),
            navigation_timeout_ms=_env_int(
                "KUAISHOU_NAVIGATION_TIMEOUT_MS", 60_000, 1_000
            ),
            license_key=os.environ.get("CLOAKBROWSER_LICENSE_KEY") or None,
            release_channel=os.environ.get("CLOAKBROWSER_RELEASE_CHANNEL") or None,
            proxy=proxy,
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "profiles").mkdir(parents=True, exist_ok=True)
        self.captures_dir.mkdir(parents=True, exist_ok=True)

    @property
    def identity_file(self) -> Path:
        return self.data_dir / "identity.json"
