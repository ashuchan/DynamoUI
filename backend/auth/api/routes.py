"""Auth REST routes — mounted under ``/api/v1/auth``."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.auth.api.dependencies import AuthContext, get_current_context
from backend.auth.dao import AuthDAO, TenantMembership
from backend.auth.models.dtos import (
    AuthResponse,
    GoogleLoginRequest,
    LoginRequest,
    MeResponse,
    SignupRequest,
    TenantSummary,
    UserSummary,
)
from backend.auth.service import (
    AuthError,
    AuthService,
    IssuedToken,
)

router = APIRouter(prefix="/auth")


def _get_service(request: Request) -> AuthService:
    svc: AuthService | None = getattr(request.app.state, "auth_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth subsystem unavailable",
        )
    return svc


def _auth_response(issued: IssuedToken) -> AuthResponse:
    return AuthResponse(
        access_token=issued.access_token,
        token_type="bearer",
        expires_in=issued.expires_in,
        user=UserSummary(
            id=issued.user.id,
            email=issued.user.email,
            display_name=issued.user.display_name,
            created_at=issued.user.created_at,
        ),
        tenant=TenantSummary(
            id=issued.active_tenant.id,
            name=issued.active_tenant.name,
            slug=issued.active_tenant.slug,
            role=issued.active_role,
        ),
        tenants=[_membership_summary(m) for m in issued.memberships],
    )


def _membership_summary(m: TenantMembership) -> TenantSummary:
    return TenantSummary(
        id=m.tenant.id, name=m.tenant.name, slug=m.tenant.slug, role=m.role
    )


@router.post("/signup", response_model=AuthResponse)
async def signup(
    payload: SignupRequest,
    svc: Annotated[AuthService, Depends(_get_service)],
) -> AuthResponse:
    try:
        issued = await svc.signup(
            email=payload.email.lower(),
            password=payload.password,
            display_name=payload.display_name,
            tenant_name=payload.tenant_name,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _auth_response(issued)


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    svc: Annotated[AuthService, Depends(_get_service)],
) -> AuthResponse:
    try:
        issued = await svc.login(email=payload.email.lower(), password=payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _auth_response(issued)


@router.post("/google", response_model=AuthResponse)
async def google_login(
    payload: GoogleLoginRequest,
    svc: Annotated[AuthService, Depends(_get_service)],
) -> AuthResponse:
    try:
        issued = await svc.google_login(id_token=payload.id_token)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _auth_response(issued)


@router.get("/me", response_model=MeResponse)
async def me(
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> MeResponse:
    dao: AuthDAO = request.app.state.auth_dao
    memberships = await dao.list_memberships(ctx.user.id)
    return MeResponse(
        user=UserSummary(
            id=ctx.user.id,
            email=ctx.user.email,
            display_name=ctx.user.display_name,
            created_at=ctx.user.created_at,
        ),
        tenant=TenantSummary(
            id=ctx.tenant.id,
            name=ctx.tenant.name,
            slug=ctx.tenant.slug,
            role=ctx.role,
        ),
        tenants=[_membership_summary(m) for m in memberships],
    )
