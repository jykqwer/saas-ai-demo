"""Agent 运行轨迹查询接口。"""

from uuid import UUID

from fastapi import APIRouter, Request, status

from core.auth import AuthenticatedUser
from core.errors import ApiError
from domain.agent import AgentTrace

router = APIRouter(prefix="/runs", tags=["agent-runs"])


@router.get(
    "/{run_id}",
    response_model=AgentTrace,
    summary="Get a persisted agent run trace",
)
async def get_run_trace(
    request: Request, run_id: UUID, user: AuthenticatedUser
) -> AgentTrace:
    trace = await request.app.state.agent_repository.get_trace(
        run_id=run_id, owner_user_id=user.id
    )
    if trace is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="RUN_NOT_FOUND",
            message="The agent run does not exist.",
        )
    return trace
