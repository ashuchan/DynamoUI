"""Canvas FastAPI dependencies.

Canvas accepts authentication EITHER via the standard ``Authorization: Bearer``
header (used by canvasClient.ts AJAX calls) OR via the ``dynamoui_canvas``
cookie (used by the plain ``<a href download>`` artifacts link, which cannot
carry a Bearer header).

The cookie is set by ``POST /session`` so the FE doesn't need to mint it
explicitly — once a Canvas session exists, the same browser session can pull
the artifacts zip.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.config import auth_settings
from backend.auth.dao import AuthDAO, TenantRow, UserRow
from backend.auth.security import TokenClaims, decode_access_token
from backend.skill_registry.config.settings import canvas_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CanvasAuthContext:
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


async def get_canvas_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    cookie_token: Annotated[str | None, Cookie(alias=canvas_settings.cookie_name)] = None,
    dao: Annotated[AuthDAO, Depends(_get_auth_dao)] = None,  # type: ignore[assignment]
) -> CanvasAuthContext:
    token: str | None = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    elif cookie_token:
        token = cookie_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing canvas auth (bearer or cookie)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access_token(token, settings=auth_settings)
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
    membership = await dao.get_membership(user.id, claims.tenant_id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user is not a member of the token's tenant",
        )
    return CanvasAuthContext(
        user=user, tenant=membership.tenant, role=membership.role, claims=claims
    )
