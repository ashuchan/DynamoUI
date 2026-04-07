# claude-auth

Module: `backend/auth/`
Role: Authentication + tenant membership. Owns `auth_tenants`, `auth_users`, `auth_tenant_users`, `auth_oauth_identities`. Issues per-tenant JWTs. Phase 1 of the multi-tenant rollout.

## Layering (strict)

```
models/tables.py         SQLAlchemy Core tables (canonical SDL)
models/dtos.py           Pydantic request / response shapes
security.py              Password hashing (stdlib scrypt) + JWT helpers (python-jose)
dao.py                   AuthDAO — every method takes identifiers explicitly
service.py               AuthService — business rules, injectable google_verifier
api/dependencies.py      get_current_context / get_current_user / get_current_tenant / require_role
api/routes.py            /api/v1/auth/{signup,login,google,me}
config.py                AuthSettings — DYNAMO_AUTH_*
```

## Non-negotiable rules

- **No ambient tenant context.** The DAO takes `tenant_id` / `user_id` explicitly. The only place the JWT is decoded is `api/dependencies.get_current_context`. Never read `tenant_id` from query strings or headers.
- **JWT is not enough.** After decoding, `get_current_context` re-verifies the membership in `auth_tenant_users`. A revoked role must take effect on the next request — do not cache this check.
- **Passwords via stdlib.** Use `security.hash_password` / `security.verify_password`. Do **not** add `bcrypt` / `passlib` / `argon2-cffi` — we deliberately use `hashlib.scrypt` to avoid another runtime dependency.
- **Google verifier is injectable.** `AuthService(google_verifier=...)` accepts any `async (id_token) -> dict` function. Tests MUST provide a mock; never hit the live Google endpoint in CI.
- **Roles.** Valid values: `owner`, `admin`, `member`, `viewer`. Enforce via `require_role("owner", "admin")` — never invent a parallel scheme.
- **Tenant uniqueness.** Signup creates a personal tenant; slug collisions are resolved by `AuthService._unique_slug` — do not bypass.

## Testing pattern

- Unit tests use a `FakeAuthDAO` (see `tests/test_auth_service.py`) so no PostgreSQL is needed.
- Cross-tenant access MUST be covered: Tenant B must never see Tenant A's membership / user / tenant rows.
- JWT tests cover roundtrip, expiry, tampering, wrong secret.

## Key JWT claim shape

```json
{
  "sub": "<user_id>",
  "tid": "<active_tenant_id>",
  "email": "alice@example.com",
  "role": "owner",
  "iat": 1712000000,
  "exp": 1712003600
}
```

Bumping the claim set requires an Alembic migration for any persisted fields plus an update to `security.decode_access_token`.
