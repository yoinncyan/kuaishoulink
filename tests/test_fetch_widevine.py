"""Unit tests for bin/fetch-widevine.py — CRX3/protobuf parsing, app-id pinning,
signature verification, the integrity-install policy, and zip extraction.

All offline: the network (`_resolve_crx`/`_download`) is mocked, and a minimal
CRX3 is synthesized in-process with a throwaway RSA key (with ``APP_ID``
monkeypatched to that key's derived id) so the real verify path is exercised
without Google's signing key.
"""

import hashlib
import importlib.util
import io
import os
import struct
import zipfile
from pathlib import Path

import pytest

# bin/fetch-widevine.py isn't importable by name (hyphen + bin/ not a package).
_FW_PATH = Path(__file__).resolve().parent.parent / "bin" / "fetch-widevine.py"
_spec = importlib.util.spec_from_file_location("fetch_widevine", _FW_PATH)
fw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fw)

crypto = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402


# ---------------------------------------------------------------------------
# protobuf primitives
# ---------------------------------------------------------------------------


def _encode_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _encode_ld(field, data):
    """Encode one length-delimited (wire type 2) protobuf field."""
    return _encode_varint((field << 3) | 2) + _encode_varint(len(data)) + data


class TestReadVarint:
    def test_single_byte(self):
        assert fw._read_varint(b"\x00", 0) == (0, 1)
        assert fw._read_varint(b"\x7f", 0) == (127, 1)

    def test_multi_byte(self):
        # 300 = 0b100101100 -> 0xAC 0x02
        assert fw._read_varint(b"\xac\x02", 0) == (300, 2)

    def test_resumes_at_offset(self):
        buf = b"\xff" + _encode_varint(16384)
        val, i = fw._read_varint(buf, 1)
        assert val == 16384 and i == len(buf)

    def test_truncated_raises(self):
        with pytest.raises(ValueError, match="truncated"):
            fw._read_varint(b"\x80\x80", 0)  # continuation bit set, runs off end

    def test_too_long_raises(self):
        with pytest.raises(ValueError, match="too long"):
            fw._read_varint(b"\x80" * 12 + b"\x01", 0)


class TestParsePb:
    def test_length_delimited_collected_repeated(self):
        blob = _encode_ld(2, b"aa") + _encode_ld(2, b"bb") + _encode_ld(1, b"c")
        out = fw._parse_pb(blob)
        assert out[2] == [b"aa", b"bb"]
        assert out[1] == [b"c"]

    def test_skips_varint_and_fixed_fields(self):
        # field 3 varint, field 4 fixed64, field 5 fixed32, then field 1 LD
        blob = (
            _encode_varint((3 << 3) | 0) + _encode_varint(99)
            + _encode_varint((4 << 3) | 1) + b"\x00" * 8
            + _encode_varint((5 << 3) | 5) + b"\x00" * 4
            + _encode_ld(1, b"x")
        )
        out = fw._parse_pb(blob)
        assert out[1] == [b"x"]
        assert 3 not in out  # varint values aren't retained


class TestArch:
    @pytest.mark.parametrize("machine", ["x86_64", "amd64", "AMD64", "x64"])
    def test_x86_64_supported(self, machine, monkeypatch):
        monkeypatch.setattr(fw.platform, "machine", lambda: machine)
        assert fw._arch() == "x64"

    @pytest.mark.parametrize("machine", ["aarch64", "arm64", "arm"])
    def test_arm_rejected_clearly(self, machine, monkeypatch):
        # Google publishes the Linux CDM for x86-64 only; arm must fail loudly.
        monkeypatch.setattr(fw.platform, "machine", lambda: machine)
        with pytest.raises(SystemExit, match="not published for linux arm64"):
            fw._arch()

    def test_unsupported_raises(self, monkeypatch):
        monkeypatch.setattr(fw.platform, "machine", lambda: "mips")
        with pytest.raises(SystemExit, match="unsupported architecture"):
            fw._arch()


class TestAppId:
    def test_constant_is_the_real_widevine_id(self):
        # Trust anchor: a typo here would silently accept the wrong publisher.
        # This exact id is what the live component server signs against (verified
        # end-to-end against update.googleapis.com).
        assert fw.APP_ID == "oimompecagnajdejgnnjijobebaeigek"


class TestCrxAppId:
    def test_matches_independent_computation(self):
        pub = b"some-der-bytes"
        digest = hashlib.sha256(pub).digest()[:16]
        expected = "".join(
            chr(0x61 + (b >> 4)) + chr(0x61 + (b & 0xF)) for b in digest
        )
        appid, digest16 = fw._crx_appid(pub)
        assert appid == expected
        assert digest16 == digest
        assert len(appid) == 32  # 16 bytes -> 32 chars, alphabet a..p
        assert all("a" <= c <= "p" for c in appid)


# ---------------------------------------------------------------------------
# CRX3 signature verification
# ---------------------------------------------------------------------------


def _build_crx3(privkey, archive=b"PK\x03\x04zip", *, crx_id=None, tamper=False):
    """Synthesize a minimal, validly-signed CRX3 for the given private key."""
    pub_der = privkey.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if crx_id is None:
        crx_id = hashlib.sha256(pub_der).digest()[:16]
    signed_header = _encode_ld(1, crx_id)  # SignedData.crx_id
    payload = (
        b"CRX3 SignedData\x00"
        + struct.pack("<I", len(signed_header))
        + signed_header
        + archive
    )
    sig = privkey.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    if tamper:
        archive = archive + b"X"  # invalidate the signature
    proof = _encode_ld(1, pub_der) + _encode_ld(2, sig)
    header = _encode_ld(2, proof) + _encode_ld(10000, signed_header)
    return b"Cr24" + struct.pack("<I", 3) + struct.pack("<I", len(header)) + header + archive


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class TestVerifyCrx3:
    def _pin(self, monkeypatch, privkey):
        pub_der = privkey.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        monkeypatch.setattr(fw, "APP_ID", fw._crx_appid(pub_der)[0])

    def test_valid_signature_accepts(self, rsa_key, monkeypatch):
        self._pin(monkeypatch, rsa_key)
        assert fw._verify_crx3(_build_crx3(rsa_key)) is True

    def test_tampered_archive_rejected(self, rsa_key, monkeypatch):
        self._pin(monkeypatch, rsa_key)
        with pytest.raises(SystemExit, match="INVALID"):
            fw._verify_crx3(_build_crx3(rsa_key, tamper=True))

    def test_wrong_publisher_key_rejected(self, rsa_key, monkeypatch):
        # APP_ID pinned to a DIFFERENT key than the one that signed.
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._pin(monkeypatch, other)
        with pytest.raises(SystemExit, match="expected Widevine publisher key"):
            fw._verify_crx3(_build_crx3(rsa_key))

    def test_crx_id_mismatch_rejected(self, rsa_key, monkeypatch):
        self._pin(monkeypatch, rsa_key)
        with pytest.raises(SystemExit, match="crx_id"):
            fw._verify_crx3(_build_crx3(rsa_key, crx_id=b"\x00" * 16))

    def test_bad_magic_rejected(self, rsa_key, monkeypatch):
        self._pin(monkeypatch, rsa_key)
        with pytest.raises(SystemExit, match="bad magic"):
            fw._verify_crx3(b"NOPE" + _build_crx3(rsa_key)[4:])

    def test_too_short_rejected(self):
        # < 12 bytes: clean SystemExit, not a raw struct.error.
        with pytest.raises(SystemExit, match="too short"):
            fw._verify_crx3(b"Cr24")

    def test_returns_false_without_cryptography(self, monkeypatch):
        # Force the inner `from cryptography...` import to fail.
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name.startswith("cryptography"):
                raise ImportError("blocked for test")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert fw._verify_crx3(b"Cr24anything") is False


# ---------------------------------------------------------------------------
# Integrity-install policy (main): refuse only when NOTHING is verifiable
# ---------------------------------------------------------------------------


class TestIntegrityPolicy:
    @pytest.mark.parametrize("sig_ok,sha,should_install", [
        (True, "deadbeef", True),    # Docker normal
        (True, None, True),          # server omitted sha; sig still verified
        (False, "deadbeef", True),   # no crypto, but sha present
        (False, None, False),        # no crypto AND no sha -> REFUSE
    ])
    def test_branches(self, sig_ok, sha, should_install, monkeypatch, tmp_path):
        monkeypatch.setattr(fw.platform, "system", lambda: "Linux")
        monkeypatch.setattr(fw, "_arch", lambda: "x64")
        monkeypatch.setattr(fw, "_resolve_crx", lambda arch: ("9.9.9", "https://x/crx", sha))
        monkeypatch.setattr(fw, "_download", lambda url, s: b"BLOB")
        monkeypatch.setattr(fw, "_verify_crx3", lambda blob: sig_ok)
        extracted = {}
        monkeypatch.setattr(fw, "_extract", lambda blob, arch, out: extracted.setdefault("out", out))

        out = tmp_path / "WidevineCdm"
        if should_install:
            assert fw.main(["--out", str(out), "--quiet"]) == 0
            assert extracted["out"] == os.path.abspath(str(out))
        else:
            with pytest.raises(SystemExit, match="refusing to install"):
                fw.main(["--out", str(out), "--quiet"])
            assert "out" not in extracted  # never reached extraction

    def test_cache_hit_skips_download(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fw.platform, "system", lambda: "Linux")
        out = tmp_path / "WidevineCdm"
        out.mkdir()
        (out / "manifest.json").write_text("{}")
        called = {"n": 0}
        monkeypatch.setattr(fw, "_resolve_crx", lambda arch: called.__setitem__("n", called["n"] + 1))
        assert fw.main(["--out", str(out), "--quiet"]) == 0
        assert called["n"] == 0  # short-circuited before any network

    def test_non_linux_refuses(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fw.platform, "system", lambda: "Darwin")
        with pytest.raises(SystemExit, match="Linux-only"):
            fw.main(["--out", str(tmp_path / "WidevineCdm"), "--quiet"])


# ---------------------------------------------------------------------------
# zip extraction — only the two expected members land; missing members fail
# ---------------------------------------------------------------------------


def _crx_with_zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    # _extract reads the zip from the end, so a raw CRX prefix isn't required.
    return buf.getvalue()


class TestExtract:
    def test_extracts_only_expected_members(self, tmp_path):
        so = "_platform_specific/linux_x64/libwidevinecdm.so"
        crx = _crx_with_zip({
            "manifest.json": b"{}",
            so: b"\x7fELF-fake",
            "evil/../../escape.txt": b"x",   # extra member must be ignored
        })
        out = tmp_path / "WidevineCdm"
        fw._extract(crx, "x64", str(out))
        assert (out / "manifest.json").is_file()
        assert (out / so).is_file()
        assert not (tmp_path / "escape.txt").exists()
        assert not (out / "evil").exists()

    def test_missing_member_raises(self, tmp_path):
        crx = _crx_with_zip({"manifest.json": b"{}"})  # no .so
        with pytest.raises(SystemExit, match="missing expected members"):
            fw._extract(crx, "x64", str(tmp_path / "WidevineCdm"))

    def test_atomic_replace_of_existing_dir(self, tmp_path):
        out = tmp_path / "WidevineCdm"
        out.mkdir()
        (out / "stale").write_text("old")
        so = "_platform_specific/linux_x64/libwidevinecdm.so"
        crx = _crx_with_zip({"manifest.json": b"{}", so: b"new"})
        fw._extract(crx, "x64", str(out))
        assert (out / "manifest.json").is_file()
        assert not (out / "stale").exists()  # old contents fully replaced
