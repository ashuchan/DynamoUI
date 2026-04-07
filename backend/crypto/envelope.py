"""AES-GCM envelope encryption with per-record DEK wrapping.

Layout of a serialised envelope (JSON, stored as TEXT in the DB):

    {
      "v": 1,                # key version
      "alg": "AES-256-GCM",
      "nonce_dek": "...",    # base64, 12 bytes — nonce used to wrap the DEK
      "wrapped_dek": "...",  # base64, AES-GCM(KEK, DEK)
      "nonce_data": "...",   # base64, 12 bytes — nonce used for the payload
      "ciphertext": "..."    # base64, AES-GCM(DEK, plaintext)
    }

Why per-record DEKs? Rotating the master key (KEK) only requires re-wrapping
each ``wrapped_dek`` — we never need to re-encrypt the (potentially large)
ciphertext payloads. The KEK never directly encrypts user data, which keeps
its usage count low and easy to audit.

The helper is intentionally narrow: ``encrypt`` / ``decrypt`` and nothing
else. Future phases must reuse this module rather than calling
``cryptography.hazmat`` directly.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass

from backend.crypto.config import CryptoSettings, crypto_settings

_ALG = "AES-256-GCM"
_DEK_LEN = 32  # 256-bit data key
_NONCE_LEN = 12  # GCM-recommended nonce length


class CryptoError(Exception):
    """Raised on any envelope failure. Message is safe to log."""


class CryptoNotConfiguredError(CryptoError):
    """Raised when the master key is missing."""


@dataclass(frozen=True)
class EnvelopePayload:
    """Lightweight wrapper around a serialised envelope string."""

    serialised: str

    def to_db(self) -> str:
        return self.serialised

    @classmethod
    def from_db(cls, value: str) -> "EnvelopePayload":
        return cls(serialised=value)


def encrypt(plaintext: str, settings: CryptoSettings | None = None) -> EnvelopePayload:
    """Encrypt ``plaintext`` and return a ready-to-store envelope."""
    s = settings or crypto_settings
    kek = _load_kek(s)

    # Local import so the cryptography dependency is only required at runtime
    # (and so test environments without the C extension can still import this
    # module for syntax checks).
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    dek = AESGCM.generate_key(bit_length=256)
    data_aead = AESGCM(dek)
    nonce_data = os.urandom(_NONCE_LEN)
    ciphertext = data_aead.encrypt(nonce_data, plaintext.encode("utf-8"), None)

    kek_aead = AESGCM(kek)
    nonce_dek = os.urandom(_NONCE_LEN)
    wrapped_dek = kek_aead.encrypt(nonce_dek, dek, None)

    payload = {
        "v": s.key_version,
        "alg": _ALG,
        "nonce_dek": _b64(nonce_dek),
        "wrapped_dek": _b64(wrapped_dek),
        "nonce_data": _b64(nonce_data),
        "ciphertext": _b64(ciphertext),
    }
    return EnvelopePayload(serialised=json.dumps(payload, separators=(",", ":")))


def decrypt(envelope: EnvelopePayload | str, settings: CryptoSettings | None = None) -> str:
    """Reverse :func:`encrypt`. Raises :class:`CryptoError` on any failure."""
    s = settings or crypto_settings
    kek = _load_kek(s)
    raw = envelope.serialised if isinstance(envelope, EnvelopePayload) else envelope

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CryptoError(f"envelope is not valid JSON: {exc}") from exc

    if payload.get("alg") != _ALG:
        raise CryptoError(f"unsupported envelope algorithm: {payload.get('alg')!r}")

    try:
        nonce_dek = _unb64(payload["nonce_dek"])
        wrapped_dek = _unb64(payload["wrapped_dek"])
        nonce_data = _unb64(payload["nonce_data"])
        ciphertext = _unb64(payload["ciphertext"])
    except (KeyError, ValueError) as exc:
        raise CryptoError(f"malformed envelope payload: {exc}") from exc

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        dek = AESGCM(kek).decrypt(nonce_dek, wrapped_dek, None)
        plaintext = AESGCM(dek).decrypt(nonce_data, ciphertext, None)
    except InvalidTag as exc:
        raise CryptoError("envelope authentication failed") from exc

    return plaintext.decode("utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_kek(settings: CryptoSettings) -> bytes:
    raw = settings.master_key.get_secret_value()
    if not raw:
        raise CryptoNotConfiguredError(
            "DYNAMO_CRYPTO_MASTER_KEY is not set; encryption is unavailable"
        )
    try:
        kek = base64.b64decode(raw, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise CryptoError(f"master key is not valid base64: {exc}") from exc
    if len(kek) != 32:
        raise CryptoError(
            f"master key must decode to 32 bytes (got {len(kek)})"
        )
    return kek


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def generate_master_key() -> str:
    """Convenience helper — print this once and store it as DYNAMO_CRYPTO_MASTER_KEY."""
    return base64.b64encode(os.urandom(32)).decode("ascii")
