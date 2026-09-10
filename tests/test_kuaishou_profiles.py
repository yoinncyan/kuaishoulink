from __future__ import annotations

import json

import pytest

from backend.kuaishou.profiles import ProfileRegistry


def write_identity(path, identity_id: str, seed: int) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "identity_id": identity_id,
                "fingerprint_seed": seed,
                "created_at": "2026-09-10T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def test_registry_adopts_legacy_profile_without_moving_session_data(tmp_path):
    profile_dir = tmp_path / "profiles" / "kuaishou"
    profile_dir.mkdir(parents=True)
    (profile_dir / "Default").mkdir()
    (profile_dir / "Default" / "Cookies").write_text("existing-login")
    legacy_identity = tmp_path / "identity.json"
    write_identity(legacy_identity, "a" * 32, 123456)

    registry = ProfileRegistry(
        tmp_path,
        legacy_profile_dir=profile_dir,
        legacy_identity_file=legacy_identity,
    )

    profiles = registry.list()
    assert profiles["active_profile_id"] == "kuaishou"
    assert profiles["profile_count"] == 1
    assert profiles["profiles"][0]["name"] == "默认账号"
    assert profiles["profiles"][0]["identity_id"] == "a" * 32
    assert profiles["profiles"][0]["fingerprint_seed"] == "123456"
    assert (profile_dir / "Default" / "Cookies").read_text() == "existing-login"
    assert legacy_identity.exists()
    assert (profile_dir / ".identity.json").exists()


def test_profiles_have_independent_identity_cache_and_activation(tmp_path):
    legacy = tmp_path / "profiles" / "kuaishou"
    registry = ProfileRegistry(
        tmp_path,
        legacy_profile_dir=legacy,
        legacy_identity_file=tmp_path / "identity.json",
    )

    account_a = registry.create("账号 A")
    account_b = registry.create("账号 B", "http://proxy.example:8080")
    dir_a = registry.profile_dir(account_a["profile_id"])
    dir_b = registry.profile_dir(account_b["profile_id"])
    (dir_a / "Cache").mkdir()
    (dir_b / "Cache").mkdir()
    (dir_a / "Cache" / "value").write_text("A")
    (dir_b / "Cache" / "value").write_text("B")

    assert account_a["identity_id"] != account_b["identity_id"]
    assert account_a["fingerprint_seed"] != account_b["fingerprint_seed"]
    assert (dir_a / "Cache" / "value").read_text() == "A"
    assert (dir_b / "Cache" / "value").read_text() == "B"
    assert account_b["proxy"] == "http://proxy.example:8080"

    activated = registry.activate(account_b["profile_id"])
    assert activated["active"] is True
    assert registry.active_profile_id == account_b["profile_id"]

    reloaded = ProfileRegistry(
        tmp_path,
        legacy_profile_dir=legacy,
        legacy_identity_file=tmp_path / "identity.json",
    )
    assert reloaded.active_profile_id == account_b["profile_id"]
    assert reloaded.get(account_b["profile_id"])["identity_id"] == (
        account_b["identity_id"]
    )

    renamed = registry.update(account_a["profile_id"], name="备用账号")
    assert renamed["name"] == "备用账号"
    deleted = registry.delete(account_a["profile_id"])
    assert deleted["deleted"] is True
    assert not dir_a.exists()
    assert dir_b.exists()


def test_registry_rejects_duplicate_names_and_active_deletion(tmp_path):
    registry = ProfileRegistry(
        tmp_path,
        legacy_profile_dir=tmp_path / "profiles" / "kuaishou",
        legacy_identity_file=tmp_path / "identity.json",
    )
    registry.create("备用账号")

    with pytest.raises(ValueError, match="名称已存在"):
        registry.create("备用账号")
    with pytest.raises(ValueError, match="不能删除"):
        registry.delete("kuaishou")
