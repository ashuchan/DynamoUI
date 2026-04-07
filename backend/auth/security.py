"""Password hashing and JWT utilities.

Password hashing uses ``hashlib.scrypt`` from the Python standard library so
we don't add a new runtime dependency. JWT encoding uses ``python-jose`` which
is already pinned in ``pyproject.toml``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from jose import JWTError, jwt

from backend.auth.config import AuthSettings, auth_settings

_SCRYPT_SCHEME = "scrypt"


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str, settings: AuthSettings | None = None) -> str:
    """Return a self-describing scrypt hash string.

    Format: ``scrypt$<n>$<r>$<p>$<b64-salt>$<b64-dk>``
    """
    s = settings or auth_settings
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=s.scrypt_n,
        r=s.scrypt_r,
        p=s.scrypt_p,
        dklen=s.scrypt_dklen,
    )
    return "$".join(
        [
            _SCRYPT_SCHEME,
            str(s.scrypt_n),
            str(s.scrypt_r),
            str(s.scrypt_p),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(dk).decode("ascii"),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of a password against a stored hash."""
    if not stored:
        return False
    try:
        scheme, n_s, r_s, p_s, salt_b64, dk_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != _SCRYPT_SCHEME:
        return False
    try:
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
    except (ValueError, base64.binascii.Error):
        return False
    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(candidate, expected)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenClaims:
    user_id: UUID
    tenant_id: UUID
    email: str
    role: str
    expires_at: int


def create_access_token(
    *,
    user_id: UUID,
    tenant_id: UUID,
    email: str,
    role: str,
    settings: AuthSettings | None = None,
    now: int | None = None,
) -> tuple[str, int]:
    """Return ``(jwt, expires_in_seconds)``."""
    s = settings or auth_settings
    issued_at = int(now if now is not None else time.time())
    exp = issued_at + s.access_token_ttl_seconds
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "email": email,
        "role": role,
        "iat": issued_at,
        "exp": exp,
    }
    token = jwt.encode(
        payload,
        s.jwt_secret.get_secret_value(),
        algorithm=s.jwt_algorithm,
    )
    return token, s.access_token_ttl_seconds


def decode_access_token(
    token: str, settings: AuthSettings | None = None
) -> TokenClaims:
    """Decode a JWT. Raises ``ValueError`` on any failure."""
    s = settings or auth_settings
    try:
        payload = jwt.decode(
            token,
            s.jwt_secret.get_secret_value(),
            algorithms=[s.jwt_algorithm],
        )
    except JWTError as exc:
        raise ValueError(f"invalid token: {exc}") from exc

    try:
        return TokenClaims(
            user_id=UUID(payload["sub"]),
            tenant_id=UUID(payload["tid"]),
            email=payload["email"],
            role=payload.get("role", "member"),
            expires_at=int(payload["exp"]),
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(f"malformed token payload: {exc}") from exc
