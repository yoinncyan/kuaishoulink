import json

from backend.kuaishou.identity import load_or_create_identity


def test_identity_is_created_and_stable(tmp_path):
    path = tmp_path / "identity.json"
    first = load_or_create_identity(path)
    second = load_or_create_identity(path)

    assert first == second
    assert first.fingerprint_seed >= 10_000
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["fingerprint_seed"] == first.fingerprint_seed


def test_invalid_identity_is_replaced(tmp_path):
    path = tmp_path / "identity.json"
    path.write_text('{"fingerprint_seed": 1, "created_at": "old"}')
    identity = load_or_create_identity(path)
    assert identity.fingerprint_seed >= 10_000
    assert identity.created_at != "old"

