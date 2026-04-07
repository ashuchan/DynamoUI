"""Service layer for tenant DB connections.

* Encrypts every persisted password via :mod:`backend.crypto.envelope`.
* Never returns plaintext credentials in any DTO it produces.
* Looks up connections strictly within the calling tenant.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

import structlog

from backend.crypto.envelope import (
    CryptoError,
    CryptoNotConfiguredError,
    EnvelopePayload,
    decrypt,
    encrypt,
)
from backend.tenants.connections.dao import ConnectionDAO, ConnectionRow
from backend.tenants.connections.dtos import (
    ConnectionCreate,
    ConnectionRead,
    ConnectionTestResult,
    ConnectionUpdate,
)

log = structlog.get_logger(__name__)

# A connection tester takes the *materialised* (decrypted) connection dict and
# returns ``None`` on success or an error string on failure. The default
# implementation is a no-op stub — adapter modules in Phase 5 will register
# real testers via the ``ConnectionService.register_tester`` API.
ConnectionTester = Callable[[dict[str, Any]], Awaitable[str | None]]


class ConnectionNotFoundError(Exception):
    pass


class DuplicateConnectionError(Exception):
    pass


class ConnectionService:
    def __init__(self, dao: ConnectionDAO) -> None:
        self._dao = dao
        self._testers: dict[str, ConnectionTester] = {}

    # ------------------------------------------------------------------
    # Tester registry
    # ------------------------------------------------------------------
    def register_tester(self, adapter_kind: str, tester: ConnectionTester) -> None:
        self._testers[adapter_kind] = tester

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    async def list(self, tenant_id: UUID) -> list[ConnectionRead]:
        rows = await self._dao.list_for_tenant(tenant_id)
        return [_to_read(r) for r in rows]

    async def get(self, tenant_id: UUID, connection_id: UUID) -> ConnectionRead:
        row = await self._dao.get(tenant_id, connection_id)
        if row is None:
            raise ConnectionNotFoundError(str(connection_id))
        return _to_read(row)

    async def create(
        self, tenant_id: UUID, payload: ConnectionCreate
    ) -> ConnectionRead:
        # Check duplicate name early so we surface a clean error.
        existing = await self._dao.list_for_tenant(tenant_id)
        if any(r.name == payload.name for r in existing):
            raise DuplicateConnectionError(payload.name)

        encrypted = _encrypt_optional(payload.password)
        row = await self._dao.insert(
            tenant_id=tenant_id,
            name=payload.name,
            adapter_kind=payload.adapter_kind,
            host=payload.host,
            port=payload.port,
            database=payload.database,
            username=payload.username,
            encrypted_secret=encrypted,
            options=payload.options,
        )
        return _to_read(row)

    async def update(
        self,
        tenant_id: UUID,
        connection_id: UUID,
        payload: ConnectionUpdate,
    ) -> ConnectionRead:
        # Build the value dict, encrypting only when a new password was supplied.
        values: dict[str, Any] = {}
        for field in ("name", "host", "port", "database", "username"):
            value = getattr(payload, field)
            if value is not None:
                values[field] = value
        if payload.options is not None:
            values["options_json"] = payload.options
        if payload.password is not None:
            values["encrypted_secret"] = _encrypt_optional(payload.password)

        if "name" in values:
            # Enforce per-tenant uniqueness of name.
            existing = await self._dao.list_for_tenant(tenant_id)
            for r in existing:
                if r.name == values["name"] and r.id != connection_id:
                    raise DuplicateConnectionError(values["name"])

        row = await self._dao.update(
            tenant_id=tenant_id, connection_id=connection_id, values=values
        )
        if row is None:
            raise ConnectionNotFoundError(str(connection_id))
        return _to_read(row)

    async def delete(self, tenant_id: UUID, connection_id: UUID) -> None:
        deleted = await self._dao.delete(tenant_id, connection_id)
        if not deleted:
            raise ConnectionNotFoundError(str(connection_id))

    # ------------------------------------------------------------------
    # Connectivity test
    # ------------------------------------------------------------------
    async def test(self, tenant_id: UUID, connection_id: UUID) -> ConnectionTestResult:
        row = await self._dao.get(tenant_id, connection_id)
        if row is None:
            raise ConnectionNotFoundError(str(connection_id))
        tester = self._testers.get(row.adapter_kind)
        if tester is None:
            return ConnectionTestResult(
                ok=False,
                status="unsupported",
                error=f"no tester registered for adapter_kind={row.adapter_kind!r}",
                tested_at=datetime.now(timezone.utc),
            )
        try:
            materialised = self.materialise(row)
        except CryptoError as exc:
            await self._dao.record_test_result(
                tenant_id=tenant_id,
                connection_id=connection_id,
                ok=False,
                error="failed to decrypt stored credential",
            )
            log.warning("connections.decrypt_failed", error=str(exc))
            return ConnectionTestResult(
                ok=False,
                status="error",
                error="failed to decrypt stored credential",
                tested_at=datetime.now(timezone.utc),
            )
        error = await tester(materialised)
        ok = error is None
        await self._dao.record_test_result(
            tenant_id=tenant_id,
            connection_id=connection_id,
            ok=ok,
            error=error,
        )
        return ConnectionTestResult(
            ok=ok,
            status="ok" if ok else "error",
            error=error,
            tested_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Internal — used by adapters wanting to actually connect.
    # ------------------------------------------------------------------
    def materialise(self, row: ConnectionRow) -> dict[str, Any]:
        """Return a dict including the decrypted password.

        WARNING: callers must NOT log or serialise this dict. Treat it as a
        single-use struct passed straight to the adapter.
        """
        password: str | None = None
        if row.encrypted_secret:
            password = decrypt(EnvelopePayload.from_db(row.encrypted_secret))
        return {
            "id": str(row.id),
            "tenant_id": str(row.tenant_id),
            "adapter_kind": row.adapter_kind,
            "host": row.host,
            "port": row.port,
            "database": row.database,
            "username": row.username,
            "password": password,
            "options": row.options,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encrypt_optional(plaintext: str | None) -> str | None:
    if plaintext is None or plaintext == "":
        return None
    try:
        return encrypt(plaintext).to_db()
    except CryptoNotConfiguredError:
        # Re-raise with a friendly message — service layer surfaces this as 503.
        raise


def _to_read(row: ConnectionRow) -> ConnectionRead:
    return ConnectionRead(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        adapter_kind=row.adapter_kind,
        host=row.host,
        port=row.port,
        database=row.database,
        username=row.username,
        has_password=row.encrypted_secret is not None,
        options=row.options,
        status=row.status,
        last_tested_at=row.last_tested_at,
        last_test_error=row.last_test_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
