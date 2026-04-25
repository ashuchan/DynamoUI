"""Unit tests for backend.auth.service.

Uses an in-memory fake DAO so the tests never touch a database. Service-level
invariants (tenant creation on signup, cross-tenant isolation, Google OAuth
flow) are exercised here.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from backend.auth.config import AuthSettings
from backend.auth.dao import TenantMembership, TenantRow, UserRow
from backend.auth.security import decode_access_token
from backend.auth.service import (
    AuthService,
    EmailAlreadyRegisteredError,
    GoogleVerificationError,
    InvalidCredentialsError,
    SignupDisabledError,
)


# ---------------------------------------------------------------------------
# In-memory fake DAO
# ---------------------------------------------------------------------------


class FakeAuthDAO:
    """In-memory stand-in for ``backend.auth.dao.AuthDAO``.

    Preserves the same method signatures so AuthService cannot distinguish it
    from the real DAO. A fake SlugStore acts as the ``_engine`` probe used by
    ``AuthService._unique_slug``.
    """

    def __init__(self) -> None:
        self.users: dict[UUID, UserRow] = {}
        self.users_by_email: dict[str, UUID] = {}
        self.tenants: dict[UUID, TenantRow] = {}
        self.memberships: dict[UUID, list[tuple[UUID, str]]] = {}  # user -> [(tenant, role)]
        self.slugs: set[str] = set()
        self.oauth: dict[tuple[str, str], UUID] = {}
        self.last_login: dict[UUID, int] = {}
        # AuthService._unique_slug reaches into ``_engine``. We fake it with a
        # shim whose ``connect()`` returns an async context manager that
        # exposes a synchronous execute() returning a row matching the slug.
        self._engine = _FakeEngine(self)

    # ---- user lookups ----
    async def get_user_by_email(self, email: str) -> UserRow | None:
        user_id = self.users_by_email.get(email.lower())
        return self.users.get(user_id) if user_id else None

    async def get_user_by_id(self, user_id: UUID) -> UserRow | None:
        return self.users.get(user_id)

    # ---- inserts ----
    async def create_user_and_tenant(
        self,
        *,
        email: str,
        password_hash: str | None,
        display_name: str,
        tenant_name: str,
        tenant_slug: str,
    ) -> tuple[UserRow, TenantRow]:
        if email.lower() in self.users_by_email:
            raise RuntimeError("duplicate email (should be caught upstream)")
        if tenant_slug in self.slugs:
            raise RuntimeError("duplicate slug (should be caught upstream)")
        user_id, tenant_id = uuid4(), uuid4()
        user = UserRow(
            id=user_id,
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            status="active",
            created_at=datetime.utcnow(),
        )
        tenant = TenantRow(
            id=tenant_id, name=tenant_name, slug=tenant_slug, status="active"
        )
        self.users[user_id] = user
        self.users_by_email[email.lower()] = user_id
        self.tenants[tenant_id] = tenant
        self.slugs.add(tenant_slug)
        self.memberships.setdefault(user_id, []).append((tenant_id, "owner"))
        return user, tenant

    async def touch_last_login(self, user_id: UUID) -> None:
        self.last_login[user_id] = self.last_login.get(user_id, 0) + 1

    async def list_memberships(self, user_id: UUID) -> list[TenantMembership]:
        out: list[TenantMembership] = []
        for tenant_id, role in self.memberships.get(user_id, []):
            out.append(TenantMembership(tenant=self.tenants[tenant_id], role=role))
        return out

    async def get_membership(
        self, user_id: UUID, tenant_id: UUID
    ) -> TenantMembership | None:
        for t_id, role in self.memberships.get(user_id, []):
            if t_id == tenant_id:
                return TenantMembership(tenant=self.tenants[t_id], role=role)
        return None

    async def get_oauth_identity(self, provider: str, subject: str) -> UUID | None:
        return self.oauth.get((provider, subject))

    async def link_oauth_identity(
        self, *, user_id: UUID, provider: str, subject: str, email: str | None
    ) -> None:
        self.oauth[(provider, subject)] = user_id


class _FakeEngine:
    def __init__(self, dao: "FakeAuthDAO") -> None:
        self._dao = dao

    def connect(self) -> "_FakeConnectionCM":
        return _FakeConnectionCM(self._dao)


class _FakeConnectionCM:
    def __init__(self, dao: "FakeAuthDAO") -> None:
        self._dao = dao

    async def __aenter__(self) -> "_FakeConnection":
        return _FakeConnection(self._dao)

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeConnection:
    def __init__(self, dao: "FakeAuthDAO") -> None:
        self._dao = dao

    async def execute(self, stmt: Any) -> "_FakeResult":
        # AuthService._unique_slug issues
        # ``sa.select(tenants.c.id).where(tenants.c.slug == candidate)``.
        # Pull the literal out of the whereclause.
        candidate = getattr(getattr(stmt, "whereclause", None), "right", None)
        candidate_value = getattr(candidate, "value", None)
        if candidate_value is not None and candidate_value in self._dao.slugs:
            return _FakeResult(has_row=True)
        return _FakeResult(has_row=False)


class _FakeResult:
    def __init__(self, has_row: bool) -> None:
        self._has_row = has_row

    def first(self) -> Any:
        return object() if self._has_row else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> AuthSettings:
    return AuthSettings(
        jwt_secret="test-secret",  # type: ignore[arg-type]
        scrypt_n=2**10,
        google_client_id="test-google-client-id",
        access_token_ttl_seconds=300,
    )


@pytest.fixture
def dao() -> FakeAuthDAO:
    return FakeAuthDAO()


@pytest.fixture
def service(dao: FakeAuthDAO, settings: AuthSettings) -> AuthService:
    async def _always_fail(id_token: str) -> dict:  # pragma: no cover — overridden per-test
        raise AssertionError("google_verifier must be mocked per-test")

    return AuthService(dao=dao, settings=settings, google_verifier=_always_fail)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Signup tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signup_creates_tenant_and_membership(
    service: AuthService, dao: FakeAuthDAO, settings: AuthSettings
) -> None:
    issued = await service.signup(
        email="alice@example.com",
        password="supersecret",
        display_name="Alice Anderson",
        tenant_name=None,
    )
    assert issued.user.email == "alice@example.com"
    assert issued.active_tenant.name == "Alice Anderson"
    assert issued.active_role == "owner"
    assert len(issued.memberships) == 1
    # Token must carry the fresh tenant id
    claims = decode_access_token(issued.access_token, settings=settings)
    assert claims.tenant_id == issued.active_tenant.id
    assert claims.user_id == issued.user.id
    assert claims.role == "owner"


@pytest.mark.asyncio
async def test_signup_rejects_duplicate_email(service: AuthService) -> None:
    await service.signup(
        email="alice@example.com",
        password="supersecret",
        display_name="Alice",
        tenant_name=None,
    )
    with pytest.raises(EmailAlreadyRegisteredError):
        await service.signup(
            email="alice@example.com",
            password="supersecret",
            display_name="Alice 2",
            tenant_name=None,
        )


@pytest.mark.asyncio
async def test_signup_disabled_raises(dao: FakeAuthDAO) -> None:
    disabled = AuthSettings(
        jwt_secret="t",  # type: ignore[arg-type]
        scrypt_n=2**10,
        signup_enabled=False,
    )
    svc = AuthService(dao=dao, settings=disabled)
    with pytest.raises(SignupDisabledError):
        await svc.signup(
            email="x@y.z", password="longenough", display_name="X", tenant_name=None
        )


@pytest.mark.asyncio
async def test_signup_unique_slug_on_collision(
    service: AuthService, dao: FakeAuthDAO
) -> None:
    await service.signup(
        email="a@example.com",
        password="supersecret",
        display_name="Acme Co",
        tenant_name=None,
    )
    issued_2 = await service.signup(
        email="b@example.com",
        password="supersecret",
        display_name="Acme Co",
        tenant_name=None,
    )
    assert issued_2.active_tenant.slug != "acme-co"
    assert issued_2.active_tenant.slug.startswith("acme-co-")


# ---------------------------------------------------------------------------
# Login tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success(service: AuthService) -> None:
    await service.signup(
        email="alice@example.com",
        password="supersecret",
        display_name="Alice",
        tenant_name=None,
    )
    issued = await service.login(email="alice@example.com", password="supersecret")
    assert issued.user.email == "alice@example.com"
    assert issued.access_token


@pytest.mark.asyncio
async def test_login_wrong_password(service: AuthService) -> None:
    await service.signup(
        email="alice@example.com",
        password="supersecret",
        display_name="Alice",
        tenant_name=None,
    )
    with pytest.raises(InvalidCredentialsError):
        await service.login(email="alice@example.com", password="wrong")


@pytest.mark.asyncio
async def test_login_unknown_user(service: AuthService) -> None:
    with pytest.raises(InvalidCredentialsError):
        await service.login(email="ghost@example.com", password="whatever")


# ---------------------------------------------------------------------------
# Google OAuth tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_signup_creates_tenant(
    dao: FakeAuthDAO, settings: AuthSettings
) -> None:
    async def verifier(id_token: str) -> dict:
        assert id_token == "fake-google-token"
        return {
            "aud": settings.google_client_id,
            "iss": "accounts.google.com",
            "exp": int(time.time()) + 600,
            "email_verified": True,
            "sub": "google-sub-123",
            "email": "bob@example.com",
            "name": "Bob Brown",
        }

    svc = AuthService(dao=dao, settings=settings, google_verifier=verifier)
    issued = await svc.google_login(id_token="fake-google-token")
    assert issued.user.email == "bob@example.com"
    assert issued.active_tenant.name == "Bob Brown"
    assert ("google", "google-sub-123") in dao.oauth


@pytest.mark.asyncio
async def test_google_login_reuses_existing_identity(
    dao: FakeAuthDAO, settings: AuthSettings
) -> None:
    async def verifier(id_token: str) -> dict:
        return {
            "aud": settings.google_client_id,
            "iss": "accounts.google.com",
            "exp": int(time.time()) + 600,
            "email_verified": True,
            "sub": "sub-42",
            "email": "carol@example.com",
            "name": "Carol",
        }

    svc = AuthService(dao=dao, settings=settings, google_verifier=verifier)
    a = await svc.google_login(id_token="tok")
    b = await svc.google_login(id_token="tok")
    assert a.user.id == b.user.id
    # Only one user should exist
    assert len(dao.users) == 1


@pytest.mark.asyncio
async def test_google_audience_mismatch_rejected(
    dao: FakeAuthDAO, settings: AuthSettings
) -> None:
    async def verifier(id_token: str) -> dict:
        return {
            "aud": "some-other-client-id",
            "iss": "accounts.google.com",
            "exp": int(time.time()) + 600,
            "email_verified": True,
            "sub": "sub",
            "email": "e@x.com",
            "name": "E",
        }

    svc = AuthService(dao=dao, settings=settings, google_verifier=verifier)
    with pytest.raises(GoogleVerificationError):
        await svc.google_login(id_token="tok")


@pytest.mark.asyncio
async def test_google_disabled_when_client_id_missing(dao: FakeAuthDAO) -> None:
    disabled = AuthSettings(
        jwt_secret="t",  # type: ignore[arg-type]
        scrypt_n=2**10,
        google_client_id="",
    )
    svc = AuthService(dao=dao, settings=disabled)
    with pytest.raises(GoogleVerificationError):
        await svc.google_login(id_token="anything")


@pytest.mark.asyncio
async def test_google_links_existing_email_user(
    service: AuthService, dao: FakeAuthDAO, settings: AuthSettings
) -> None:
    # Pre-create via email signup
    await service.signup(
        email="d@example.com",
        password="supersecret",
        display_name="Dana",
        tenant_name=None,
    )
    assert len(dao.users) == 1

    async def verifier(id_token: str) -> dict:
        return {
            "aud": settings.google_client_id,
            "iss": "accounts.google.com",
            "exp": int(time.time()) + 600,
            "email_verified": True,
            "sub": "dana-sub",
            "email": "d@example.com",
            "name": "Dana",
        }

    svc = AuthService(dao=dao, settings=settings, google_verifier=verifier)
    issued = await svc.google_login(id_token="tok")
    assert issued.user.email == "d@example.com"
    # Should link, not duplicate
    assert len(dao.users) == 1
    assert ("google", "dana-sub") in dao.oauth


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_membership_lookup_denied(
    service: AuthService, dao: FakeAuthDAO
) -> None:
    a = await service.signup(
        email="a@example.com",
        password="supersecret",
        display_name="A",
        tenant_name=None,
    )
    b = await service.signup(
        email="b@example.com",
        password="supersecret",
        display_name="B",
        tenant_name=None,
    )
    # User A must not be a member of B's tenant, and vice versa.
    assert await dao.get_membership(a.user.id, b.active_tenant.id) is None
    assert await dao.get_membership(b.user.id, a.active_tenant.id) is None
    # But each is a member of their own tenant.
    assert await dao.get_membership(a.user.id, a.active_tenant.id) is not None
    assert await dao.get_membership(b.user.id, b.active_tenant.id) is not None
