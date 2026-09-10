"""Persistent registry for isolated CloakBrowser account profiles."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .identity import BrowserIdentity, load_or_create_identity


PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,47}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProfileRegistry:
    """Own profile metadata and keep each browser identity on separate storage."""

    def __init__(
        self,
        data_dir: Path,
        *,
        legacy_profile_dir: Path,
        legacy_identity_file: Path,
    ) -> None:
        self.data_dir = data_dir
        self.root = data_dir / "profiles"
        self.registry_path = self.root / "registry.json"
        self.legacy_profile_dir = legacy_profile_dir
        self.legacy_identity_file = legacy_identity_file
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self._payload = self._load_or_initialize()

    @staticmethod
    def _validate_name(name: str) -> str:
        normalized = " ".join(name.strip().split())
        if not normalized or len(normalized) > 60:
            raise ValueError("Profile 名称长度必须为 1–60 个字符")
        return normalized

    @staticmethod
    def _validate_profile_id(profile_id: str) -> str:
        normalized = profile_id.strip().lower()
        if not PROFILE_ID_RE.fullmatch(normalized):
            raise ValueError("无效的 Profile ID")
        return normalized

    def _atomic_save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._payload["updated_at"] = utc_now()
        temporary = self.registry_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, self.registry_path)

    def _load_or_initialize(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
            profiles = list(payload.get("profiles") or [])
            active = str(payload.get("active_profile_id") or "")
            ids = {str(profile.get("profile_id") or "") for profile in profiles}
            if profiles and active in ids:
                return payload
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

        now = utc_now()
        self.legacy_profile_dir.mkdir(parents=True, exist_ok=True)
        identity_target = self.legacy_profile_dir / ".identity.json"
        if self.legacy_identity_file.exists() and not identity_target.exists():
            # Copy rather than move so an older already-running service keeps
            # reading its original identity path until the controlled restart.
            shutil.copy2(self.legacy_identity_file, identity_target)
        load_or_create_identity(identity_target)
        payload = {
            "schema_version": 1,
            "active_profile_id": "kuaishou",
            "created_at": now,
            "updated_at": now,
            "profiles": [
                {
                    "profile_id": "kuaishou",
                    "name": "默认账号",
                    "directory": self.legacy_profile_dir.name,
                    "proxy": None,
                    "created_at": now,
                    "updated_at": now,
                    "last_used_at": now,
                }
            ],
        }
        self._payload = payload
        self._atomic_save()
        return payload

    def _profile_record(self, profile_id: str) -> dict[str, Any]:
        normalized = self._validate_profile_id(profile_id)
        for profile in self._payload.get("profiles") or []:
            if profile.get("profile_id") == normalized:
                return profile
        raise KeyError(f"Profile 不存在：{normalized}")

    def profile_dir(self, profile_id: str) -> Path:
        profile = self._profile_record(profile_id)
        directory = self.root / str(profile["directory"])
        resolved_root = self.root.resolve()
        resolved = directory.resolve()
        if resolved == resolved_root or resolved_root not in resolved.parents:
            raise RuntimeError("Profile 目录越界")
        return directory

    def identity_file(self, profile_id: str) -> Path:
        return self.profile_dir(profile_id) / ".identity.json"

    def identity(self, profile_id: str) -> BrowserIdentity:
        return load_or_create_identity(self.identity_file(profile_id))

    @property
    def active_profile_id(self) -> str:
        return str(self._payload["active_profile_id"])

    def active_profile(self) -> dict[str, Any]:
        return self.get(self.active_profile_id)

    def get(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            profile = dict(self._profile_record(profile_id))
            identity = self.identity(profile["profile_id"])
            return self._public(profile, identity)

    def _public(
        self, profile: dict[str, Any], identity: BrowserIdentity
    ) -> dict[str, Any]:
        profile_id = str(profile["profile_id"])
        return {
            "profile_id": profile_id,
            "name": profile["name"],
            "active": profile_id == self.active_profile_id,
            "proxy": profile.get("proxy"),
            "created_at": profile.get("created_at"),
            "updated_at": profile.get("updated_at"),
            "last_used_at": profile.get("last_used_at"),
            "profile_dir": str(self.profile_dir(profile_id)),
            "identity_id": identity.identity_id,
            # JSON/JavaScript cannot represent the full 63-bit seed exactly.
            "fingerprint_seed": str(identity.fingerprint_seed),
            "identity_created_at": identity.created_at,
        }

    def list(self) -> dict[str, Any]:
        with self._lock:
            profiles = [
                self._public(dict(profile), self.identity(str(profile["profile_id"])))
                for profile in self._payload.get("profiles") or []
            ]
            profiles.sort(key=lambda row: (not row["active"], row["created_at"] or ""))
            return {
                "active_profile_id": self.active_profile_id,
                "profile_count": len(profiles),
                "profiles": profiles,
            }

    def create(self, name: str, proxy: str | None = None) -> dict[str, Any]:
        with self._lock:
            normalized_name = self._validate_name(name)
            if any(
                str(profile.get("name") or "").casefold()
                == normalized_name.casefold()
                for profile in self._payload.get("profiles") or []
            ):
                raise ValueError("Profile 名称已存在")
            profile_id = f"profile-{secrets.token_hex(6)}"
            while any(
                profile.get("profile_id") == profile_id
                for profile in self._payload.get("profiles") or []
            ):
                profile_id = f"profile-{secrets.token_hex(6)}"
            now = utc_now()
            directory = self.root / profile_id
            directory.mkdir(parents=True, exist_ok=False)
            try:
                directory.chmod(0o700)
            except OSError:
                pass
            identity = load_or_create_identity(directory / ".identity.json")
            profile = {
                "profile_id": profile_id,
                "name": normalized_name,
                "directory": profile_id,
                "proxy": proxy.strip() if proxy and proxy.strip() else None,
                "created_at": now,
                "updated_at": now,
                "last_used_at": None,
            }
            self._payload.setdefault("profiles", []).append(profile)
            self._atomic_save()
            return self._public(profile, identity)

    def update(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        proxy: str | None = None,
        update_proxy: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            profile = self._profile_record(profile_id)
            if name is not None:
                normalized_name = self._validate_name(name)
                if any(
                    other is not profile
                    and str(other.get("name") or "").casefold()
                    == normalized_name.casefold()
                    for other in self._payload.get("profiles") or []
                ):
                    raise ValueError("Profile 名称已存在")
                profile["name"] = normalized_name
            if update_proxy:
                profile["proxy"] = proxy.strip() if proxy and proxy.strip() else None
            profile["updated_at"] = utc_now()
            self._atomic_save()
            return self._public(profile, self.identity(profile["profile_id"]))

    def activate(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            profile = self._profile_record(profile_id)
            now = utc_now()
            self._payload["active_profile_id"] = profile["profile_id"]
            profile["last_used_at"] = now
            profile["updated_at"] = now
            self._atomic_save()
            return self._public(profile, self.identity(profile["profile_id"]))

    def delete(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            profile = self._profile_record(profile_id)
            if profile["profile_id"] == self.active_profile_id:
                raise ValueError("当前使用中的 Profile 不能删除，请先切换")
            if len(self._payload.get("profiles") or []) <= 1:
                raise ValueError("至少保留一个 Profile")
            identity = self.identity(profile["profile_id"])
            directory = self.profile_dir(profile["profile_id"])
            self._payload["profiles"] = [
                row
                for row in self._payload.get("profiles") or []
                if row.get("profile_id") != profile["profile_id"]
            ]
            self._atomic_save()
            shutil.rmtree(directory, ignore_errors=False)
            return {
                "profile_id": profile["profile_id"],
                "name": profile["name"],
                "identity_id": identity.identity_id,
                "deleted": True,
            }
