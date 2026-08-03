"""认证安全原语、审批状态和并发配额测试。"""

import asyncio
from datetime import date, datetime, timedelta, timezone

from core.security import hash_access_token, hash_password, verify_password
from infrastructure.auth_repository import EphemeralAuthRepository


def _run(coro):
    """独立运行协程，并为仓库中的旧同步测试保留一个当前事件循环。"""

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def test_password_hash_roundtrip() -> None:
    encoded = hash_password("CorrectHorse123!")
    assert "CorrectHorse123!" not in encoded
    assert verify_password("CorrectHorse123!", encoded)
    assert not verify_password("wrong-password", encoded)


def test_registration_requires_approval_before_authentication() -> None:
    async def scenario() -> None:
        repo = EphemeralAuthRepository()
        admin = await repo.ensure_superuser(
            username="admin", password_hash=hash_password("AdminPass123!")
        )
        user = await repo.create_user(
            username="alice", password_hash=hash_password("AlicePass123!")
        )
        assert user is not None
        assert user.status == "pending"

        token_hash = hash_access_token("pending-token")
        await repo.create_auth_session(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert await repo.authenticate(token_hash=token_hash) is None

        approved = await repo.set_status(
            user_id=user.id, status="approved", approved_by=admin.id
        )
        assert approved is not None
        assert approved.status == "approved"
        authenticated = await repo.authenticate(token_hash=token_hash)
        assert authenticated is not None
        assert authenticated.username == "alice"

    _run(scenario())


def test_daily_question_limit_is_atomic() -> None:
    async def scenario() -> None:
        repo = EphemeralAuthRepository()
        user = await repo.create_user(
            username="quota-user", password_hash=hash_password("QuotaPass123!")
        )
        assert user is not None
        today = date(2026, 8, 3)
        results = await asyncio.gather(
            *(
                repo.consume_question(user_id=user.id, usage_date=today, limit=10)
                for _ in range(20)
            )
        )
        assert sum(value is not None for value in results) == 10
        assert await repo.get_usage(user_id=user.id, usage_date=today) == 10

    _run(scenario())
