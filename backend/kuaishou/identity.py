"""Persistent CloakBrowser fingerprint identity for one isolated Profile."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class BrowserIdentity:
    identity_id: str
    fingerprint_seed: int
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "identity_id": self.identity_id,
            "fingerprint_seed": self.fingerprint_seed,
            "created_at": self.created_at,
        }


def load_or_create_identity(path: Path) -> BrowserIdentity:
    """Load a stable seed, creating a cryptographically random one if absent."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        identity_id = str(payload["identity_id"])
        seed = int(payload["fingerprint_seed"])
        created_at = str(payload["created_at"])
        if (
            len(identity_id) == 32
            and all(character in "0123456789abcdef" for character in identity_id)
            and 10_000 <= seed < 2**63
            and created_at
        ):
            return BrowserIdentity(identity_id, seed, created_at)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    identity = BrowserIdentity(
        identity_id=secrets.token_hex(16),
        fingerprint_seed=secrets.randbelow(2**63 - 10_000) + 10_000,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(identity.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    return identity
