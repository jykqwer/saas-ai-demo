"""组装 v1 生产 API，确保新增业务端点统一挂载在 `/api/v1` 下。"""

from fastapi import APIRouter

from api.v1.auth import router as auth_router
from api.v1.chat import router as chat_router
from api.v1.health import router as health_router
from api.v1.knowledge import router as knowledge_router
from api.v1.runs import router as runs_router
from api.v1.sessions import router as sessions_router

router = APIRouter(prefix="/api/v1")
router.include_router(health_router)
router.include_router(auth_router)
router.include_router(chat_router)
router.include_router(sessions_router)
router.include_router(knowledge_router)
router.include_router(runs_router)
