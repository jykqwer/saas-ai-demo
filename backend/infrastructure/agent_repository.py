"""Agent Run/Step/Event 的 PostgreSQL 与内存仓库。"""

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.agent import AgentEvent, AgentRun, AgentStep, AgentTrace, StepKind
from infrastructure.database import AgentEventRow, AgentRunRow, AgentStepRow


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run(row: AgentRunRow) -> AgentRun:
    return AgentRun(
        id=row.id,
        session_id=row.session_id,
        owner_user_id=row.owner_user_id,
        status=row.status,  # type: ignore[arg-type]
        mode=row.mode,
        input_text=row.input_text,
        final_output=row.final_output,
        provider=row.provider,
        model=row.model,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _step(row: AgentStepRow) -> AgentStep:
    return AgentStep(
        id=row.id,
        run_id=row.run_id,
        sequence=row.sequence,
        kind=row.kind,  # type: ignore[arg-type]
        name=row.name,
        status=row.status,  # type: ignore[arg-type]
        input_data=row.input_data,
        output_data=row.output_data,
        latency_ms=row.latency_ms,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def _event(row: AgentEventRow) -> AgentEvent:
    return AgentEvent(
        id=row.id,
        run_id=row.run_id,
        sequence=row.sequence,
        event_type=row.event_type,
        payload=row.payload,
        created_at=row.created_at,
    )


class SqlAlchemyAgentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_run(
        self, *, session_id: UUID, owner_user_id: UUID, mode: str, input_text: str
    ) -> AgentRun:
        async with self._session_factory() as session:
            row = AgentRunRow(
                session_id=session_id,
                owner_user_id=owner_user_id,
                mode=mode,
                input_text=input_text,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _run(row)

    async def get_trace(
        self, *, run_id: UUID, owner_user_id: UUID
    ) -> AgentTrace | None:
        async with self._session_factory() as session:
            run_row = (
                await session.execute(
                    select(AgentRunRow).where(
                        AgentRunRow.id == run_id,
                        AgentRunRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if run_row is None:
                return None
            steps = (
                (
                    await session.execute(
                        select(AgentStepRow)
                        .where(AgentStepRow.run_id == run_id)
                        .order_by(AgentStepRow.sequence)
                    )
                )
                .scalars()
                .all()
            )
            events = (
                (
                    await session.execute(
                        select(AgentEventRow)
                        .where(AgentEventRow.run_id == run_id)
                        .order_by(AgentEventRow.sequence)
                    )
                )
                .scalars()
                .all()
            )
            return AgentTrace(
                run=_run(run_row),
                steps=[_step(row) for row in steps],
                events=[_event(row) for row in events],
            )

    async def mark_running(self, *, run_id: UUID) -> None:
        async with self._session_factory() as session:
            row = await session.get(AgentRunRow, run_id)
            if row is not None:
                row.status = "running"
                row.updated_at = _now()
                await session.commit()

    async def _next_sequence(
        self, session: AsyncSession, row_type, run_id: UUID
    ) -> int:
        value = (
            await session.execute(
                select(func.coalesce(func.max(row_type.sequence), 0)).where(
                    row_type.run_id == run_id
                )
            )
        ).scalar_one()
        return int(value) + 1

    async def start_step(
        self,
        *,
        run_id: UUID,
        kind: StepKind,
        name: str,
        input_data: dict[str, Any] | None = None,
    ) -> AgentStep:
        async with self._session_factory() as session:
            row = AgentStepRow(
                run_id=run_id,
                sequence=await self._next_sequence(session, AgentStepRow, run_id),
                kind=kind,
                name=name,
                input_data=input_data,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _step(row)

    async def complete_step(
        self,
        *,
        step_id: UUID,
        output_data: dict[str, Any] | None,
        latency_ms: int,
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(AgentStepRow, step_id)
            if row is not None:
                row.status = "completed"
                row.output_data = output_data
                row.latency_ms = latency_ms
                row.completed_at = _now()
                await session.commit()

    async def fail_step(
        self, *, step_id: UUID, code: str, message: str, latency_ms: int
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(AgentStepRow, step_id)
            if row is not None:
                row.status = "failed"
                row.error_code = code
                row.error_message = message[:300]
                row.latency_ms = latency_ms
                row.completed_at = _now()
                await session.commit()

    async def append_event(
        self, *, run_id: UUID, event_type: str, payload: dict[str, Any]
    ) -> AgentEvent:
        async with self._session_factory() as session:
            row = AgentEventRow(
                run_id=run_id,
                sequence=await self._next_sequence(session, AgentEventRow, run_id),
                event_type=event_type,
                payload=payload,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _event(row)

    async def complete_run(
        self,
        *,
        run_id: UUID,
        final_output: str,
        provider: str,
        model: str,
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(AgentRunRow, run_id)
            if row is not None:
                now = _now()
                row.status = "completed"
                row.final_output = final_output
                row.provider = provider
                row.model = model
                row.updated_at = now
                row.completed_at = now
                await session.commit()

    async def fail_run(self, *, run_id: UUID, code: str, message: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(AgentRunRow, run_id)
            if row is not None:
                now = _now()
                row.status = "failed"
                row.error_code = code
                row.error_message = message[:300]
                row.updated_at = now
                row.completed_at = now
                await session.commit()

    async def cancel_run(self, *, run_id: UUID) -> None:
        async with self._session_factory() as session:
            row = await session.get(AgentRunRow, run_id)
            if row is not None:
                now = _now()
                row.status = "cancelled"
                row.error_code = "RUN_CANCELLED"
                row.error_message = "The client disconnected before completion."
                row.updated_at = now
                row.completed_at = now
                await session.commit()


class EphemeralAgentRepository:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._runs: dict[UUID, AgentRun] = {}
        self._steps: dict[UUID, list[AgentStep]] = defaultdict(list)
        self._events: dict[UUID, list[AgentEvent]] = defaultdict(list)

    async def create_run(self, *, session_id, owner_user_id, mode, input_text):
        now = _now()
        run = AgentRun(
            id=uuid4(),
            session_id=session_id,
            owner_user_id=owner_user_id,
            status="queued",
            mode=mode,
            input_text=input_text,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._runs[run.id] = run
        return run

    async def get_trace(self, *, run_id, owner_user_id):
        run = self._runs.get(run_id)
        if run is None or run.owner_user_id != owner_user_id:
            return None
        return AgentTrace(
            run=run, steps=list(self._steps[run_id]), events=list(self._events[run_id])
        )

    async def mark_running(self, *, run_id):
        async with self._lock:
            self._runs[run_id] = self._runs[run_id].model_copy(
                update={"status": "running", "updated_at": _now()}
            )

    async def start_step(self, *, run_id, kind, name, input_data=None):
        step = AgentStep(
            id=uuid4(),
            run_id=run_id,
            sequence=len(self._steps[run_id]) + 1,
            kind=kind,
            name=name,
            status="running",
            input_data=input_data,
            created_at=_now(),
        )
        async with self._lock:
            self._steps[run_id].append(step)
        return step

    async def complete_step(self, *, step_id, output_data, latency_ms):
        await self._update_step(
            step_id,
            status="completed",
            output_data=output_data,
            latency_ms=latency_ms,
            completed_at=_now(),
        )

    async def fail_step(self, *, step_id, code, message, latency_ms):
        await self._update_step(
            step_id,
            status="failed",
            error_code=code,
            error_message=message[:300],
            latency_ms=latency_ms,
            completed_at=_now(),
        )

    async def _update_step(self, step_id, **updates):
        async with self._lock:
            for steps in self._steps.values():
                for index, step in enumerate(steps):
                    if step.id == step_id:
                        steps[index] = step.model_copy(update=updates)
                        return

    async def append_event(self, *, run_id, event_type, payload):
        event = AgentEvent(
            id=uuid4(),
            run_id=run_id,
            sequence=len(self._events[run_id]) + 1,
            event_type=event_type,
            payload=payload,
            created_at=_now(),
        )
        async with self._lock:
            self._events[run_id].append(event)
        return event

    async def complete_run(self, *, run_id, final_output, provider, model):
        now = _now()
        async with self._lock:
            self._runs[run_id] = self._runs[run_id].model_copy(
                update={
                    "status": "completed",
                    "final_output": final_output,
                    "provider": provider,
                    "model": model,
                    "updated_at": now,
                    "completed_at": now,
                }
            )

    async def fail_run(self, *, run_id, code, message):
        now = _now()
        async with self._lock:
            self._runs[run_id] = self._runs[run_id].model_copy(
                update={
                    "status": "failed",
                    "error_code": code,
                    "error_message": message[:300],
                    "updated_at": now,
                    "completed_at": now,
                }
            )

    async def cancel_run(self, *, run_id):
        now = _now()
        async with self._lock:
            self._runs[run_id] = self._runs[run_id].model_copy(
                update={
                    "status": "cancelled",
                    "error_code": "RUN_CANCELLED",
                    "error_message": "The client disconnected before completion.",
                    "updated_at": now,
                    "completed_at": now,
                }
            )
