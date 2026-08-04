"""持久化 Agent 运行、步骤与事件的领域模型。"""

from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
StepStatus = Literal["running", "completed", "failed"]
StepKind = Literal["model_call", "tool_call"]


class AgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    session_id: UUID
    owner_user_id: UUID
    status: RunStatus
    mode: str
    input_text: str
    final_output: str | None = None
    provider: str | None = None
    model: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class AgentStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    run_id: UUID
    sequence: int
    kind: StepKind
    name: str
    status: StepStatus
    input_data: dict[str, Any] | None = None
    output_data: dict[str, Any] | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    run_id: UUID
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class AgentTrace(BaseModel):
    run: AgentRun
    steps: list[AgentStep]
    events: list[AgentEvent]


class AgentRepository(Protocol):
    async def create_run(
        self, *, session_id: UUID, owner_user_id: UUID, mode: str, input_text: str
    ) -> AgentRun: ...

    async def get_trace(
        self, *, run_id: UUID, owner_user_id: UUID
    ) -> AgentTrace | None: ...

    async def mark_running(self, *, run_id: UUID) -> None: ...

    async def start_step(
        self,
        *,
        run_id: UUID,
        kind: StepKind,
        name: str,
        input_data: dict[str, Any] | None = None,
    ) -> AgentStep: ...

    async def complete_step(
        self,
        *,
        step_id: UUID,
        output_data: dict[str, Any] | None,
        latency_ms: int,
    ) -> None: ...

    async def fail_step(
        self, *, step_id: UUID, code: str, message: str, latency_ms: int
    ) -> None: ...

    async def append_event(
        self, *, run_id: UUID, event_type: str, payload: dict[str, Any]
    ) -> AgentEvent: ...

    async def complete_run(
        self,
        *,
        run_id: UUID,
        final_output: str,
        provider: str,
        model: str,
    ) -> None: ...

    async def fail_run(self, *, run_id: UUID, code: str, message: str) -> None: ...

    async def cancel_run(self, *, run_id: UUID) -> None: ...
