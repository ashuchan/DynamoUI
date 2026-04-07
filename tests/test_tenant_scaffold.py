"""Service-level tests for backend.tenants.scaffold.

Uses fake DAOs for both connections and scaffold jobs so the tests don't
need a database. The orchestration is what we want to lock down here:

* start() refuses to create a job for someone else's connection
* run() invokes the registered scaffolder for the connection's adapter_kind
* run() records progress and a final completed/failed status
* cross-tenant get/list is impossible
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
from backend.tenants.connections.dtos import ConnectionCreate
from backend.tenants.connections.service import (
    ConnectionNotFoundError,
    ConnectionService,
)
from backend.tenants.scaffold.dao import ScaffoldJobRow
from backend.tenants.scaffold.dtos import ScaffoldStartRequest
from backend.tenants.scaffold.service import ScaffoldNotFoundError, ScaffoldService

from tests.test_tenant_connections import FakeConnectionDAO


class FakeScaffoldDAO:
    def __init__(self) -> None:
        self.rows: dict[UUID, ScaffoldJobRow] = {}

    async def create(self, *, tenant_id: UUID, connection_id: UUID) -> ScaffoldJobRow:
        new_id = uuid4()
        now = datetime.now(timezone.utc)
        row = ScaffoldJobRow(
            id=new_id,
            tenant_id=tenant_id,
            connection_id=connection_id,
            status="pending",
            progress=0,
            result_summary=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        self.rows[new_id] = row
        return row

    async def get(self, tenant_id: UUID, job_id: UUID) -> ScaffoldJobRow | None:
        row = self.rows.get(job_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    async def list_for_tenant(self, tenant_id: UUID) -> list[ScaffoldJobRow]:
        return [r for r in self.rows.values() if r.tenant_id == tenant_id]

    async def update_progress(
        self,
        *,
        tenant_id: UUID,
        job_id: UUID,
        status: str,
        progress: int,
        result_summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        row = self.rows.get(job_id)
        if row is None or row.tenant_id != tenant_id:
            return
        self.rows[job_id] = ScaffoldJobRow(
            **{
                **row.__dict__,
                "status": status,
                "progress": progress,
                "result_summary": result_summary,
                "error": error,
                "updated_at": datetime.now(timezone.utc),
            }
        )


@pytest.fixture
def crypto_settings(monkeypatch: pytest.MonkeyPatch) -> CryptoSettings:
    s = CryptoSettings(master_key=generate_master_key())  # type: ignore[arg-type]
    monkeypatch.setattr(envelope_module, "crypto_settings", s)
    return s


@pytest.fixture
def connection_service(crypto_settings: CryptoSettings) -> ConnectionService:
    return ConnectionService(dao=FakeConnectionDAO())  # type: ignore[arg-type]


@pytest.fixture
def scaffold_service(connection_service: ConnectionService) -> ScaffoldService:
    return ScaffoldService(
        dao=FakeScaffoldDAO(),  # type: ignore[arg-type]
        connection_service=connection_service,
    )


@pytest.mark.asyncio
async def test_start_creates_pending_job(
    scaffold_service: ScaffoldService, connection_service: ConnectionService
) -> None:
    tenant = uuid4()
    conn = await connection_service.create(
        tenant, ConnectionCreate(name="prod", adapter_kind="postgresql")
    )
    job = await scaffold_service.start(
        tenant_id=tenant, connection_id=conn.id, request=ScaffoldStartRequest()
    )
    assert job.status == "pending"
    assert job.progress == 0
    assert job.connection_id == conn.id


@pytest.mark.asyncio
async def test_start_rejects_cross_tenant_connection(
    scaffold_service: ScaffoldService, connection_service: ConnectionService
) -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    conn = await connection_service.create(
        tenant_a, ConnectionCreate(name="prod", adapter_kind="postgresql")
    )
    with pytest.raises(ConnectionNotFoundError):
        await scaffold_service.start(
            tenant_id=tenant_b,
            connection_id=conn.id,
            request=ScaffoldStartRequest(),
        )


@pytest.mark.asyncio
async def test_run_uses_registered_scaffolder(
    scaffold_service: ScaffoldService, connection_service: ConnectionService
) -> None:
    tenant = uuid4()
    conn = await connection_service.create(
        tenant,
        ConnectionCreate(name="prod", adapter_kind="dynamodb", password="pw"),
    )
    job = await scaffold_service.start(
        tenant_id=tenant, connection_id=conn.id, request=ScaffoldStartRequest()
    )

    captured: dict[str, Any] = {}

    class _Spy:
        async def scaffold(self, *, connection, request, progress):
            captured["connection"] = connection
            captured["request"] = request
            await progress(50)
            return {"tables_inspected": ["users"], "skills_generated": 1}

    scaffold_service.register_scaffolder("dynamodb", _Spy())
    await scaffold_service.run(
        tenant_id=tenant,
        job_id=job.id,
        connection_id=conn.id,
        request=ScaffoldStartRequest(),
    )

    refreshed = await scaffold_service.get(tenant, job.id)
    assert refreshed.status == "completed"
    assert refreshed.progress == 100
    assert refreshed.result_summary == {
        "tables_inspected": ["users"],
        "skills_generated": 1,
    }
    assert captured["connection"]["password"] == "pw"
    assert captured["connection"]["adapter_kind"] == "dynamodb"


@pytest.mark.asyncio
async def test_run_records_failure(
    scaffold_service: ScaffoldService, connection_service: ConnectionService
) -> None:
    tenant = uuid4()
    conn = await connection_service.create(
        tenant, ConnectionCreate(name="prod", adapter_kind="postgresql")
    )
    job = await scaffold_service.start(
        tenant_id=tenant, connection_id=conn.id, request=ScaffoldStartRequest()
    )

    class _Boom:
        async def scaffold(self, *, connection, request, progress):
            raise RuntimeError("inspector exploded")

    scaffold_service.register_scaffolder("postgresql", _Boom())
    await scaffold_service.run(
        tenant_id=tenant,
        job_id=job.id,
        connection_id=conn.id,
        request=ScaffoldStartRequest(),
    )

    refreshed = await scaffold_service.get(tenant, job.id)
    assert refreshed.status == "failed"
    assert refreshed.error == "inspector exploded"


@pytest.mark.asyncio
async def test_get_cross_tenant_denied(
    scaffold_service: ScaffoldService, connection_service: ConnectionService
) -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    conn = await connection_service.create(
        tenant_a, ConnectionCreate(name="prod", adapter_kind="postgresql")
    )
    job = await scaffold_service.start(
        tenant_id=tenant_a,
        connection_id=conn.id,
        request=ScaffoldStartRequest(),
    )
    with pytest.raises(ScaffoldNotFoundError):
        await scaffold_service.get(tenant_b, job.id)
