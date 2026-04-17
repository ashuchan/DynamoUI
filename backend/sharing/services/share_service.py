"""Share token service — issue, verify, revoke."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.sharing.models.tables import share_tokens


def _hash_token(token: str) -> str:
    # bcrypt would be ideal but would add a dependency; SHA-256 is enough
    # given the token is 32 random bytes of entropy. If bcrypt is present we
    # upgrade transparently.
    try:
        import bcrypt  # type: ignore

        return bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        return hashlib.sha256(token.encode()).hexdigest()


def _verify_token(token: str, stored: str) -> bool:
    try:
        import bcrypt  # type: ignore

        if stored.startswith("$2"):
            return bcrypt.checkpw(token.encode(), stored.encode())
    except ImportError:
        pass
    return hashlib.sha256(token.encode()).hexdigest() == stored


class ShareTokenNotFound(Exception):
    pass


class ShareExpired(Exception):
    pass


class ShareService:
    def __init__(self, engine: AsyncEngine, *, app_base_url: str = "") -> None:
        self._engine = engine
        self._base_url = app_base_url.rstrip("/")

    async def create(
        self,
        *,
        source_type: str,
        source_id: str,
        user_id: UUID,
        tenant_id: UUID,
        expires_in_seconds: int | None,
        max_access_count: int | None,
    ) -> dict:
        token = secrets.token_urlsafe(32)
        row_id = uuid4()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
            if expires_in_seconds
            else None
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(share_tokens).values(
                    id=row_id,
                    source_type=source_type,
                    source_id=source_id,
                    token_hash=_hash_token(token),
                    created_by_user_id=user_id,
                    tenant_id=tenant_id,
                    expires_at=expires_at,
                    max_access_count=max_access_count,
                )
            )
        return {
            "id": str(row_id),
            "token": token,
            "url": f"{self._base_url}/api/v1/shared/{token}",
            "embedUrl": f"{self._base_url}/embed/{token}",
            "expiresAt": expires_at.isoformat() if expires_at else None,
        }

    async def list(
        self, *, source_type: str | None, source_id: str | None, user_id: UUID
    ) -> list[dict]:
        async with self._engine.connect() as conn:
            stmt = sa.select(share_tokens).where(
                share_tokens.c.created_by_user_id == user_id
            )
            if source_type:
                stmt = stmt.where(share_tokens.c.source_type == source_type)
            if source_id:
                stmt = stmt.where(share_tokens.c.source_id == source_id)
            rows = (await conn.execute(stmt)).mappings().all()
        return [
            {
                "id": str(r["id"]),
                "sourceType": r["source_type"],
                "sourceId": r["source_id"],
                "createdAt": r["created_at"].isoformat(),
                "expiresAt": r["expires_at"].isoformat() if r["expires_at"] else None,
                "accessCount": r["access_count"],
                "maxAccessCount": r["max_access_count"],
            }
            for r in rows
        ]

    async def delete(self, token_id: UUID, *, user_id: UUID) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(share_tokens).where(
                    share_tokens.c.id == token_id,
                    share_tokens.c.created_by_user_id == user_id,
                )
            )

    async def resolve(self, token: str) -> dict:
        """Resolve a token to its (source_type, source_id). Raises on invalid/expired."""
        async with self._engine.begin() as conn:
            rows = (
                await conn.execute(sa.select(share_tokens))
            ).mappings().all()
            match = next((r for r in rows if _verify_token(token, r["token_hash"])), None)
            if match is None:
                raise ShareTokenNotFound("invalid token")
            if match["expires_at"] and match["expires_at"] < datetime.now(timezone.utc):
                raise ShareExpired("token expired")
            if (
                match["max_access_count"] is not None
                and match["access_count"] >= match["max_access_count"]
            ):
                raise ShareExpired("token exhausted")
            await conn.execute(
                sa.update(share_tokens)
                .where(share_tokens.c.id == match["id"])
                .values(access_count=match["access_count"] + 1)
            )
        return {
            "sourceType": match["source_type"],
            "sourceId": match["source_id"],
        }
