"""Service-level tests for backend.tenants.connections.

Uses an in-memory fake DAO so the tests don't need PostgreSQL. Cross-tenant
isolation is exercised explicitly: Tenant A must never be able to read,
update, delete, or test Tenant B's connection.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

cryptography = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")

from backend.crypto.config import CryptoSettings
from backend.crypto.envelope import generate_master_key
import backend.crypto.envelope as envelope_module
from backend.tenants.connections.dao import ConnectionRow
from backend.tenants.connections.dtos import ConnectionCreate, ConnectionUpdate
from backend.tenants.connections.service import (
    ConnectionNotFoundError,
    ConnectionService,
    DuplicateConnectionError,
)


# ---------------------------------------------------------------------------
# In-memory DAO
# ---------------------------------------------------------------------------


class FakeConnectionDAO:
    def __init__(self) -> None:
        self.rows: dict[UUID, ConnectionRow] = {}

    async def list_for_tenant(self, tenant_id: UUID) -> list[ConnectionRow]:
        return [r for r in self.rows.values() if r.tenant_id == tenant_id]

    async def get(self, tenant_id: UUID, connection_id: UUID) -> ConnectionRow | None:
        row = self.rows.get(connection_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    async def insert(
        self,
        *,
        tenant_id: UUID,
        name: str,
        adapter_kind: str,
        host: str | None,
        port: int | None,
        database: str | None,
        username: str | None,
        encrypted_secret: str | None,
        options: dict[str, Any],
    ) -> ConnectionRow:
        new_id = uuid4()
        now = datetime.now(timezone.utc)
        row = ConnectionRow(
            id=new_id,
            tenant_id=tenant_id,
            name=name,
            adapter_kind=adapter_kind,
            host=host,
            port=port,
            database=database,
            username=username,
            encrypted_secret=encrypted_secret,
            options=options,
            status="untested",
            last_tested_at=None,
            last_test_error=None,
            created_at=now,
            updated_at=now,
        )
        self.rows[new_id] = row
        return row

    async def update(
        self,
        *,
        tenant_id: UUID,
        connection_id: UUID,
        values: dict[str, Any],
    ) -> ConnectionRow | None:
        row = self.rows.get(connection_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        # Apply field renames so the fake matches the real DAO's update path.
        new = dict(
            id=row.id,
            tenant_id=row.tenant_id,
            name=values.get("name", row.name),
            adapter_kind=row.adapter_kind,
            host=values.get("host", row.host),
            port=values.get("port", row.port),
            database=values.get("database", row.database),
            username=values.get("username", row.username),
            encrypted_secret=values.get("encrypted_secret", row.encrypted_secret),
            options=values.get("options_json", row.options),
            status=row.status,
            last_tested_at=row.last_tested_at,
            last_test_error=row.last_test_error,
            created_at=row.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        updated = ConnectionRow(**new)
        self.rows[connection_id] = updated
        return updated

    async def delete(self, tenant_id: UUID, connection_id: UUID) -> bool:
        row = self.rows.get(connection_id)
        if row is None or row.tenant_id != tenant_id:
            return False
        del self.rows[connection_id]
        return True

    async def record_test_result(
        self,
        *,
        tenant_id: UUID,
        connection_id: UUID,
        ok: bool,
        error: str | None,
    ) -> None:
        row = self.rows.get(connection_id)
        if row is None or row.tenant_id != tenant_id:
            return
        self.rows[connection_id] = ConnectionRow(
            **{
                **row.__dict__,
                "status": "ok" if ok else "error",
                "last_tested_at": datetime.now(timezone.utc),
                "last_test_error": None if ok else error,
                "updated_at": datetime.now(timezone.utc),
            }
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def crypto_settings(monkeypatch: pytest.MonkeyPatch) -> CryptoSettings:
    s = CryptoSettings(master_key=generate_master_key())  # type: ignore[arg-type]
    # The service module reads the module-level singleton; patch it.
    monkeypatch.setattr(envelope_module, "crypto_settings", s)
    return s


@pytest.fixture
def dao() -> FakeConnectionDAO:
    return FakeConnectionDAO()


@pytest.fixture
def service(dao: FakeConnectionDAO, crypto_settings: CryptoSettings) -> ConnectionService:
    return ConnectionService(dao=dao)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_encrypts_password_and_hides_it(service: ConnectionService) -> None:
    tenant = uuid4()
    payload = ConnectionCreate(
        name="prod-pg",
        adapter_kind="postgresql",
        host="db.example.com",
        port=5432,
        database="app",
        username="reader",
        password="hunter2",
    )
    created = await service.create(tenant, payload)
    assert created.has_password is True
    # Read response must not leak the plaintext anywhere.
    dumped = created.model_dump_json()
    assert "hunter2" not in dumped


@pytest.mark.asyncio
async def test_duplicate_name_rejected(service: ConnectionService) -> None:
    tenant = uuid4()
    base = ConnectionCreate(name="prod", adapter_kind="postgresql", password="x")
    await service.create(tenant, base)
    with pytest.raises(DuplicateConnectionError):
        await service.create(tenant, base)


@pytest.mark.asyncio
async def test_update_password_rotates_envelope(
    service: ConnectionService, dao: FakeConnectionDAO
) -> None:
    tenant = uuid4()
    created = await service.create(
        tenant,
        ConnectionCreate(name="prod", adapter_kind="postgresql", password="first"),
    )
    first_envelope = dao.rows[created.id].encrypted_secret

    await service.update(
        tenant,
        created.id,
        ConnectionUpdate(password="second"),
    )
    second_envelope = dao.rows[created.id].encrypted_secret

    assert first_envelope != second_envelope
    materialised = service.materialise(dao.rows[created.id])
    assert materialised["password"] == "second"


@pytest.mark.asyncio
async def test_delete_removes_record(service: ConnectionService, dao: FakeConnectionDAO) -> None:
    tenant = uuid4()
    created = await service.create(
        tenant, ConnectionCreate(name="prod", adapter_kind="postgresql")
    )
    await service.delete(tenant, created.id)
    with pytest.raises(ConnectionNotFoundError):
        await service.get(tenant, created.id)


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_get_denied(service: ConnectionService) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    a_conn = await service.create(
        tenant_a, ConnectionCreate(name="prod", adapter_kind="postgresql")
    )
    with pytest.raises(ConnectionNotFoundError):
        await service.get(tenant_b, a_conn.id)


@pytest.mark.asyncio
async def test_cross_tenant_update_denied(service: ConnectionService) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    a_conn = await service.create(
        tenant_a, ConnectionCreate(name="prod", adapter_kind="postgresql")
    )
    with pytest.raises(ConnectionNotFoundError):
        await service.update(tenant_b, a_conn.id, ConnectionUpdate(host="evil.example.com"))


@pytest.mark.asyncio
async def test_cross_tenant_delete_denied(service: ConnectionService) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    a_conn = await service.create(
        tenant_a, ConnectionCreate(name="prod", adapter_kind="postgresql")
    )
    with pytest.raises(ConnectionNotFoundError):
        await service.delete(tenant_b, a_conn.id)


@pytest.mark.asyncio
async def test_list_only_returns_calling_tenant_records(service: ConnectionService) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    await service.create(
        tenant_a, ConnectionCreate(name="a", adapter_kind="postgresql")
    )
    await service.create(
        tenant_b, ConnectionCreate(name="b", adapter_kind="postgresql")
    )
    a_list = await service.list(tenant_a)
    b_list = await service.list(tenant_b)
    assert {c.name for c in a_list} == {"a"}
    assert {c.name for c in b_list} == {"b"}


# ---------------------------------------------------------------------------
# Tester registration + test endpoint behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_returns_unsupported_when_no_tester(service: ConnectionService) -> None:
    tenant = uuid4()
    created = await service.create(
        tenant, ConnectionCreate(name="prod", adapter_kind="quantumdb")
    )
    result = await service.test(tenant, created.id)
    assert result.ok is False
    assert result.status == "unsupported"


@pytest.mark.asyncio
async def test_tester_receives_decrypted_payload(service: ConnectionService) -> None:
    tenant = uuid4()
    created = await service.create(
        tenant,
        ConnectionCreate(name="prod", adapter_kind="postgresql", password="hunter2"),
    )

    captured: dict[str, Any] = {}

    async def tester(payload: dict[str, Any]) -> str | None:
        captured.update(payload)
        return None

    service.register_tester("postgresql", tester)
    result = await service.test(tenant, created.id)
    assert result.ok is True
    assert captured["password"] == "hunter2"


@pytest.mark.asyncio
async def test_failing_tester_records_error(service: ConnectionService) -> None:
    tenant = uuid4()
    created = await service.create(
        tenant, ConnectionCreate(name="prod", adapter_kind="postgresql", password="x")
    )

    async def tester(payload: dict[str, Any]) -> str | None:
        return "connection refused"

    service.register_tester("postgresql", tester)
    result = await service.test(tenant, created.id)
    assert result.ok is False
    assert result.error == "connection refused"
    refreshed = await service.get(tenant, created.id)
    assert refreshed.status == "error"
    assert refreshed.last_test_error == "connection refused"
