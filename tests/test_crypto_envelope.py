"""Tests for backend.crypto.envelope.

These rely on the ``cryptography`` library being importable. If it isn't,
the entire module is skipped — the prod environment always has it via
``python-jose[cryptography]``.
"""
from __future__ import annotations

import base64

import pytest

cryptography = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")

from backend.crypto.config import CryptoSettings
from backend.crypto.envelope import (
    CryptoError,
    CryptoNotConfiguredError,
    EnvelopePayload,
    decrypt,
    encrypt,
    generate_master_key,
)


@pytest.fixture
def settings() -> CryptoSettings:
    return CryptoSettings(master_key=generate_master_key())  # type: ignore[arg-type]


def test_roundtrip(settings: CryptoSettings) -> None:
    payload = encrypt("super secret 🔐", settings)
    assert payload.serialised  # non-empty
    assert "super secret" not in payload.serialised  # not stored as plaintext
    assert decrypt(payload, settings) == "super secret 🔐"


def test_each_encrypt_uses_unique_nonce(settings: CryptoSettings) -> None:
    a = encrypt("same", settings)
    b = encrypt("same", settings)
    assert a.serialised != b.serialised  # nonces + DEKs differ
    assert decrypt(a, settings) == "same"
    assert decrypt(b, settings) == "same"


def test_decrypt_wrong_key_fails(settings: CryptoSettings) -> None:
    payload = encrypt("hello", settings)
    other = CryptoSettings(master_key=generate_master_key())  # type: ignore[arg-type]
    with pytest.raises(CryptoError):
        decrypt(payload, other)


def test_unconfigured_master_key_raises() -> None:
    s = CryptoSettings(master_key="")  # type: ignore[arg-type]
    with pytest.raises(CryptoNotConfiguredError):
        encrypt("anything", s)


def test_invalid_master_key_length_rejected() -> None:
    short = base64.b64encode(b"abc").decode()
    s = CryptoSettings(master_key=short)  # type: ignore[arg-type]
    with pytest.raises(CryptoError):
        encrypt("hello", s)


def test_envelope_from_db_roundtrip(settings: CryptoSettings) -> None:
    payload = encrypt("hello", settings)
    db_value = payload.to_db()
    rebuilt = EnvelopePayload.from_db(db_value)
    assert decrypt(rebuilt, settings) == "hello"


def test_decrypt_rejects_garbage(settings: CryptoSettings) -> None:
    with pytest.raises(CryptoError):
        decrypt("not-json", settings)


def test_decrypt_rejects_wrong_algorithm(settings: CryptoSettings) -> None:
    import json

    bad = json.dumps(
        {"v": 1, "alg": "RC4", "nonce_dek": "", "wrapped_dek": "", "nonce_data": "", "ciphertext": ""}
    )
    with pytest.raises(CryptoError):
        decrypt(bad, settings)
