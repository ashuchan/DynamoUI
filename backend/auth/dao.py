"""Auth DAO — all DB access for the auth subsystem.

Every function takes its identifiers explicitly so the service layer cannot
accidentally leak data across tenants. There is NO ambient request context
here — that's the ``dependencies`` module's job.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.auth.models.tables import (
    oauth_identities,
    tenant_users,
    tenants,
    users,
)


# ---------------------------------------------------------------------------
# Row dataclasses — deliberately narrow, no secrets in the read shapes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantRow:
    id: UUID
    name: str
    slug: str
    status: str


@dataclass(frozen=True)
class UserRow:
    id: UUID
    email: str
    display_name: str | None
    password_hash: str | None
    status: str
    created_at: datetime


@dataclass(frozen=True)
class TenantMembership:
    tenant: TenantRow
    role: str


class AuthDAO:
    """Layered data access for auth tables. No business rules here."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # User lookups
    # ------------------------------------------------------------------
    async def get_user_by_email(self, email: str) -> UserRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(users).where(
                        sa.func.lower(users.c.email) == email.lower()
                    )
                )
            ).mappings().first()
        return _user_row(row) if row else None

    async def get_user_by_id(self, user_id: UUID) -> UserRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(users).where(users.c.id == user_id)
                )
            ).mappings().first()
        return _user_row(row) if row else None

    # ------------------------------------------------------------------
    # Inserts — wrapped in a single transaction via ``signup``.
    # ------------------------------------------------------------------
    async def create_user_and_tenant(
        self,
        *,
        email: str,
        password_hash: str | None,
        display_name: str,
        tenant_name: str,
        tenant_slug: str,
    ) -> tuple[UserRow, TenantRow]:
        user_id = uuid4()
        tenant_id = uuid4()
        async with self._engine.begin() as conn:
            # Uniqueness on email is enforced by the DB; we still do a pre-check
            # to return a cleaner error in the service layer.
            await conn.execute(
                sa.insert(users).values(
                    id=user_id,
                    email=email,
                    password_hash=password_hash,
                    display_name=display_name,
                )
            )
            await conn.execute(
                sa.insert(tenants).values(
                    id=tenant_id,
                    name=tenant_name,
                    slug=tenant_slug,
                )
            )
            await conn.execute(
                sa.insert(tenant_users).values(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role="owner",
                )
            )
        return (
            UserRow(
                id=user_id,
                email=email,
                display_name=display_name,
                password_hash=password_hash,
                status="active",
                created_at=datetime.utcnow(),
            ),
            TenantRow(id=tenant_id, name=tenant_name, slug=tenant_slug, status="active"),
        )

    async def touch_last_login(self, user_id: UUID) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(users)
                .where(users.c.id == user_id)
                .values(last_login_at=sa.func.now())
            )

    # ------------------------------------------------------------------
    # Tenant membership lookups — the ONLY place tenant access is resolved.
    # ------------------------------------------------------------------
    async def list_memberships(self, user_id: UUID) -> list[TenantMembership]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(
                        tenants.c.id,
                        tenants.c.name,
                        tenants.c.slug,
                        tenants.c.status,
                        tenant_users.c.role,
                    )
                    .select_from(
                        tenant_users.join(
                            tenants, tenant_users.c.tenant_id == tenants.c.id
                        )
                    )
                    .where(tenant_users.c.user_id == user_id)
                    .order_by(tenants.c.created_at.asc())
                )
            ).mappings().all()
        return [
            TenantMembership(
                tenant=TenantRow(
                    id=r["id"], name=r["name"], slug=r["slug"], status=r["status"]
                ),
                role=r["role"],
            )
            for r in rows
        ]

    async def get_membership(
        self, user_id: UUID, tenant_id: UUID
    ) -> TenantMembership | None:
        """Return the user's membership in ``tenant_id`` or ``None``.

        This is the SINGLE point where we decide whether the user is allowed
        to act on behalf of a given tenant. Callers must NEVER derive access
        from any other source.
        """
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(
                        tenants.c.id,
                        tenants.c.name,
                        tenants.c.slug,
                        tenants.c.status,
                        tenant_users.c.role,
                    )
                    .select_from(
                        tenant_users.join(
                            tenants, tenant_users.c.tenant_id == tenants.c.id
                        )
                    )
                    .where(
                        tenant_users.c.user_id == user_id,
                        tenant_users.c.tenant_id == tenant_id,
                    )
                )
            ).mappings().first()
        if not row:
            return None
        return TenantMembership(
            tenant=TenantRow(
                id=row["id"], name=row["name"], slug=row["slug"], status=row["status"]
            ),
            role=row["role"],
        )

    # ------------------------------------------------------------------
    # OAuth identities
    # ------------------------------------------------------------------
    async def get_oauth_identity(
        self, provider: str, subject: str
    ) -> UUID | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(oauth_identities.c.user_id).where(
                        oauth_identities.c.provider == provider,
                        oauth_identities.c.subject == subject,
                    )
                )
            ).mappings().first()
        return row["user_id"] if row else None

    async def link_oauth_identity(
        self, *, user_id: UUID, provider: str, subject: str, email: str | None
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(oauth_identities).values(
                    id=uuid4(),
                    user_id=user_id,
                    provider=provider,
                    subject=subject,
                    email=email,
                )
            )


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------
def _user_row(row: sa.engine.RowMapping) -> UserRow:
    return UserRow(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        password_hash=row["password_hash"],
        status=row["status"],
        created_at=row["created_at"],
    )


def memberships_to_summaries(
    memberships: Iterable[TenantMembership],
) -> list[dict]:
    return [
        {
            "id": m.tenant.id,
            "name": m.tenant.name,
            "slug": m.tenant.slug,
            "role": m.role,
        }
        for m in memberships
    ]
