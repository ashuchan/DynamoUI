"""FastAPI dependencies — current user + current tenant.

All tenant-scoped routes should depend on ``get_current_tenant``. Never read
``tenant_id`` from query strings or headers — only from the verified JWT.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.config import auth_settings
from backend.auth.dao import AuthDAO, TenantRow, UserRow
from backend.auth.security import TokenClaims, decode_access_token

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    user: UserRow
    tenant: TenantRow
    role: str
    claims: TokenClaims


def _get_auth_dao(request: Request) -> AuthDAO:
    dao: AuthDAO | None = getattr(request.app.state, "auth_dao", None)
    if dao is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth subsystem unavailable",
        )
    return dao


async def get_current_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    dao: Annotated[AuthDAO, Depends(_get_auth_dao)],
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access_token(credentials.credentials, settings=auth_settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await dao.get_user_by_id(claims.user_id)
    if user is None or user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found"
        )

    # Re-verify membership on every request — JWT alone is not sufficient
    # because role can be revoked between issuance and use.
    membership = await dao.get_membership(user.id, claims.tenant_id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user is not a member of the token's tenant",
        )

    return AuthContext(
        user=user,
        tenant=membership.tenant,
        role=membership.role,
        claims=claims,
    )


async def get_current_user(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> UserRow:
    return ctx.user


async def get_current_tenant(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> TenantRow:
    return ctx.tenant


def require_role(*allowed: str):
    """Dependency factory — guard a route against unprivileged roles."""
    allowed_set = set(allowed)

    async def _guard(
        ctx: Annotated[AuthContext, Depends(get_current_context)],
    ) -> AuthContext:
        if ctx.role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires one of roles: {sorted(allowed_set)}",
            )
        return ctx

    return _guard
