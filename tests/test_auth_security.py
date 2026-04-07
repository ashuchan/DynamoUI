"""Unit tests for backend.auth.security — password hashing + JWT."""
from __future__ import annotations

import time
from uuid import uuid4

import pytest

from backend.auth.config import AuthSettings
from backend.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


@pytest.fixture
def fast_settings() -> AuthSettings:
    """scrypt at a low cost factor so tests stay fast."""
    return AuthSettings(
        jwt_secret="test-secret",  # type: ignore[arg-type]
        scrypt_n=2**10,
        scrypt_r=8,
        scrypt_p=1,
        access_token_ttl_seconds=60,
    )


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_hash_password_roundtrip(fast_settings: AuthSettings) -> None:
    stored = hash_password("correct horse battery staple", fast_settings)
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong password", stored)


def test_hash_password_unique_per_call(fast_settings: AuthSettings) -> None:
    a = hash_password("same-pw", fast_settings)
    b = hash_password("same-pw", fast_settings)
    assert a != b  # salt is per-call
    assert verify_password("same-pw", a)
    assert verify_password("same-pw", b)


def test_verify_rejects_malformed_hash() -> None:
    assert not verify_password("anything", "")
    assert not verify_password("anything", "not-a-hash")
    assert not verify_password("anything", "bcrypt$...")


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def test_token_roundtrip(fast_settings: AuthSettings) -> None:
    user_id, tenant_id = uuid4(), uuid4()
    token, ttl = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        email="alice@example.com",
        role="owner",
        settings=fast_settings,
    )
    assert ttl == fast_settings.access_token_ttl_seconds

    claims = decode_access_token(token, settings=fast_settings)
    assert claims.user_id == user_id
    assert claims.tenant_id == tenant_id
    assert claims.email == "alice@example.com"
    assert claims.role == "owner"


def test_decode_rejects_tampered_token(fast_settings: AuthSettings) -> None:
    token, _ = create_access_token(
        user_id=uuid4(),
        tenant_id=uuid4(),
        email="a@b.c",
        role="owner",
        settings=fast_settings,
    )
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(ValueError):
        decode_access_token(tampered, settings=fast_settings)


def test_decode_rejects_expired_token(fast_settings: AuthSettings) -> None:
    token, _ = create_access_token(
        user_id=uuid4(),
        tenant_id=uuid4(),
        email="a@b.c",
        role="owner",
        settings=fast_settings,
        now=int(time.time()) - 10_000,
    )
    with pytest.raises(ValueError):
        decode_access_token(token, settings=fast_settings)


def test_decode_rejects_wrong_secret(fast_settings: AuthSettings) -> None:
    token, _ = create_access_token(
        user_id=uuid4(),
        tenant_id=uuid4(),
        email="a@b.c",
        role="owner",
        settings=fast_settings,
    )
    other = AuthSettings(
        jwt_secret="different-secret",  # type: ignore[arg-type]
        scrypt_n=2**10,
    )
    with pytest.raises(ValueError):
        decode_access_token(token, settings=other)
