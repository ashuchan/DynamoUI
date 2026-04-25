"""Auth service layer — orchestrates DAO calls + token issuance.

The Google verifier is injected so tests never hit the live endpoint. In
production, ``_default_google_verifier`` performs a blocking ``urllib``
lookup against Google's tokeninfo endpoint and is executed in a thread via
``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

import structlog

from backend.auth.config import AuthSettings, auth_settings
from backend.auth.dao import AuthDAO, TenantMembership, TenantRow, UserRow
from backend.auth.security import (
    create_access_token,
    hash_password,
    verify_password,
)

log = structlog.get_logger(__name__)


class AuthError(Exception):
    """Base auth error. Message is safe to surface to the client."""

    status_code: int = 400


class InvalidCredentialsError(AuthError):
    status_code = 401


class SignupDisabledError(AuthError):
    status_code = 403


class EmailAlreadyRegisteredError(AuthError):
    status_code = 409


class GoogleVerificationError(AuthError):
    status_code = 401


GoogleVerifier = Callable[[str], Awaitable[dict]]


@dataclass
class IssuedToken:
    access_token: str
    expires_in: int
    user: UserRow
    active_tenant: TenantRow
    active_role: str
    memberships: list[TenantMembership]


class AuthService:
    """High-level operations invoked by the REST layer."""

    def __init__(
        self,
        dao: AuthDAO,
        settings: AuthSettings | None = None,
        google_verifier: GoogleVerifier | None = None,
    ) -> None:
        self._dao = dao
        self._settings = settings or auth_settings
        self._google_verifier = google_verifier or _default_google_verifier

    # ------------------------------------------------------------------
    # Email + password signup / login
    # ------------------------------------------------------------------
    async def signup(
        self, *, email: str, password: str, display_name: str, tenant_name: str | None
    ) -> IssuedToken:
        if not self._settings.signup_enabled:
            raise SignupDisabledError("public signups are currently disabled")

        existing = await self._dao.get_user_by_email(email)
        if existing:
            raise EmailAlreadyRegisteredError("email is already registered")

        effective_tenant_name = tenant_name or display_name
        slug = await self._unique_slug(effective_tenant_name)

        password_hash = hash_password(password, self._settings)
        user, tenant = await self._dao.create_user_and_tenant(
            email=email,
            password_hash=password_hash,
            display_name=display_name,
            tenant_name=effective_tenant_name,
            tenant_slug=slug,
        )
        return self._issue_for(user, tenant, "owner")

    async def login(self, *, email: str, password: str) -> IssuedToken:
        user = await self._dao.get_user_by_email(email)
        if not user or not user.password_hash:
            raise InvalidCredentialsError("invalid email or password")
        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("invalid email or password")
        memberships = await self._dao.list_memberships(user.id)
        if not memberships:
            raise InvalidCredentialsError("user has no active tenant")
        await self._dao.touch_last_login(user.id)
        active = memberships[0]
        return self._finalise_token(user, active.tenant, active.role, memberships)

    # ------------------------------------------------------------------
    # Google OAuth
    # ------------------------------------------------------------------
    async def google_login(self, *, id_token: str) -> IssuedToken:
        if not self._settings.google_client_id:
            raise GoogleVerificationError("google login is not configured")
        try:
            payload = await self._google_verifier(id_token)
        except Exception as exc:  # noqa: BLE001
            raise GoogleVerificationError(f"google verification failed: {exc}") from exc

        aud = payload.get("aud")
        if aud != self._settings.google_client_id:
            raise GoogleVerificationError("google token audience mismatch")
        iss = payload.get("iss")
        if iss not in ("accounts.google.com", "https://accounts.google.com"):
            raise GoogleVerificationError("google token issuer mismatch")
        exp_raw = payload.get("exp")
        try:
            exp_ts = int(exp_raw) if exp_raw is not None else None
        except (TypeError, ValueError) as exc:
            raise GoogleVerificationError("google token missing valid exp") from exc
        if exp_ts is None or exp_ts <= int(datetime.now(timezone.utc).timestamp()):
            raise GoogleVerificationError("google token expired")
        if payload.get("email_verified") not in (True, "true", "True"):
            raise GoogleVerificationError("google email not verified")
        subject = payload.get("sub")
        email = payload.get("email")
        if not subject or not email:
            raise GoogleVerificationError("google token missing sub or email")
        display_name = payload.get("name") or email.split("@", 1)[0]

        existing_user_id = await self._dao.get_oauth_identity("google", subject)
        if existing_user_id:
            user = await self._dao.get_user_by_id(existing_user_id)
            if user is None:  # pragma: no cover — data integrity
                raise InvalidCredentialsError("linked user was removed")
            memberships = await self._dao.list_memberships(user.id)
            await self._dao.touch_last_login(user.id)
        else:
            existing_by_email = await self._dao.get_user_by_email(email)
            if existing_by_email:
                await self._dao.link_oauth_identity(
                    user_id=existing_by_email.id,
                    provider="google",
                    subject=subject,
                    email=email,
                )
                user = existing_by_email
                memberships = await self._dao.list_memberships(user.id)
                await self._dao.touch_last_login(user.id)
            else:
                slug = await self._unique_slug(display_name)
                user, tenant = await self._dao.create_user_and_tenant(
                    email=email,
                    password_hash=None,
                    display_name=display_name,
                    tenant_name=display_name,
                    tenant_slug=slug,
                )
                await self._dao.link_oauth_identity(
                    user_id=user.id,
                    provider="google",
                    subject=subject,
                    email=email,
                )
                memberships = await self._dao.list_memberships(user.id)

        if not memberships:
            raise InvalidCredentialsError("user has no active tenant")
        active = memberships[0]
        return self._finalise_token(user, active.tenant, active.role, memberships)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _finalise_token(
        self,
        user: UserRow,
        tenant: TenantRow,
        role: str,
        memberships: list[TenantMembership],
    ) -> IssuedToken:
        token, ttl = create_access_token(
            user_id=user.id,
            tenant_id=tenant.id,
            email=user.email,
            role=role,
            settings=self._settings,
        )
        return IssuedToken(
            access_token=token,
            expires_in=ttl,
            user=user,
            active_tenant=tenant,
            active_role=role,
            memberships=memberships,
        )

    def _issue_for(
        self, user: UserRow, tenant: TenantRow, role: str
    ) -> IssuedToken:
        return self._finalise_token(
            user,
            tenant,
            role,
            [TenantMembership(tenant=tenant, role=role)],
        )

    async def _unique_slug(self, tenant_name: str) -> str:
        base = _slugify(tenant_name) or "tenant"
        candidate = base
        counter = 1
        # The slug uniqueness constraint is enforced by the DB. We probe a few
        # candidates client-side so the common case returns a clean value.
        from backend.auth.models.tables import tenants
        import sqlalchemy as sa

        async with self._dao._engine.connect() as conn:  # noqa: SLF001
            while True:
                row = (
                    await conn.execute(
                        sa.select(tenants.c.id).where(tenants.c.slug == candidate)
                    )
                ).first()
                if row is None:
                    return candidate
                counter += 1
                candidate = f"{base}-{counter}"
                if counter > 1000:  # pragma: no cover — sanity
                    raise RuntimeError("exhausted slug candidates")


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------
_slug_re = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    normalised = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    slug = _slug_re.sub("-", normalised.lower()).strip("-")
    return slug[:48]


# ---------------------------------------------------------------------------
# Default Google verifier — thin wrapper over the tokeninfo endpoint.
# ---------------------------------------------------------------------------


async def _default_google_verifier(id_token: str) -> dict:
    """Verify a Google ID token by calling the tokeninfo endpoint.

    We deliberately avoid the ``google-auth`` library so we don't add a
    runtime dependency. The tokeninfo endpoint itself performs signature
    verification server-side — the ``aud`` check is repeated in
    :meth:`AuthService.google_login`.
    """

    def _fetch() -> dict:
        url = (
            f"{auth_settings.google_tokeninfo_url}?"
            + urlencode({"id_token": id_token})
        )
        req = Request(url, headers={"User-Agent": "dynamoui-auth/1.0"})
        with urlopen(req, timeout=5) as resp:  # noqa: S310 — fixed host
            body = resp.read().decode("utf-8")
        return json.loads(body)

    return await asyncio.to_thread(_fetch)
