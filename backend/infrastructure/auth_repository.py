"""用户、审批、登录会话与每日问答配额仓库。"""

import asyncio
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.user import UserAccount, UserStatus
from infrastructure.database import AuthSessionRow, DailyUsageRow, UserRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_user(row: UserRow) -> UserAccount:
    return UserAccount(
        id=row.id,
        username=row.username,
        role=row.role,
        status=row.status,
        created_at=row.created_at,
        approved_at=row.approved_at,
    )


class SqlAlchemyAuthRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_user(
        self, *, username: str, password_hash: str
    ) -> UserAccount | None:
        async with self._session_factory() as session:
            row = UserRow(
                username=username,
                password_hash=password_hash,
                role="user",
                status="pending",
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            await session.refresh(row)
            return _to_user(row)

    async def get_credentials(self, *, username: str) -> tuple[UserAccount, str] | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(UserRow).where(UserRow.username == username)
                )
            ).scalar_one_or_none()
            return (_to_user(row), row.password_hash) if row is not None else None

    async def get_user(self, *, user_id: UUID) -> UserAccount | None:
        async with self._session_factory() as session:
            row = await session.get(UserRow, user_id)
            return _to_user(row) if row is not None else None

    async def ensure_superuser(
        self, *, username: str, password_hash: str
    ) -> UserAccount:
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(UserRow).where(UserRow.username == username)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.role != "superuser":
                    raise ValueError(
                        "bootstrap superuser username is already registered"
                    )
                return _to_user(existing)
            now = _utcnow()
            row = UserRow(
                username=username,
                password_hash=password_hash,
                role="superuser",
                status="approved",
                approved_at=now,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_user(row)

    async def create_auth_session(
        self, *, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                AuthSessionRow(
                    user_id=user_id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                )
            )
            await session.commit()

    async def authenticate(self, *, token_hash: str) -> UserAccount | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(UserRow)
                    .join(AuthSessionRow, AuthSessionRow.user_id == UserRow.id)
                    .where(
                        AuthSessionRow.token_hash == token_hash,
                        AuthSessionRow.expires_at > _utcnow(),
                        UserRow.status == "approved",
                    )
                )
            ).scalar_one_or_none()
            return _to_user(row) if row is not None else None

    async def revoke(self, *, token_hash: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(AuthSessionRow).where(AuthSessionRow.token_hash == token_hash)
            )
            await session.commit()

    async def list_users(
        self, *, status: UserStatus | None = None
    ) -> list[UserAccount]:
        async with self._session_factory() as session:
            query = select(UserRow).order_by(UserRow.created_at.desc())
            if status is not None:
                query = query.where(UserRow.status == status)
            rows = (await session.execute(query)).scalars().all()
            return [_to_user(row) for row in rows]

    async def set_status(
        self, *, user_id: UUID, status: UserStatus, approved_by: UUID
    ) -> UserAccount | None:
        async with self._session_factory() as session:
            row = await session.get(UserRow, user_id)
            if row is None or row.role == "superuser":
                return None
            row.status = status
            row.approved_by = approved_by
            row.approved_at = _utcnow() if status == "approved" else None
            if status != "approved":
                await session.execute(
                    delete(AuthSessionRow).where(AuthSessionRow.user_id == user_id)
                )
            await session.commit()
            await session.refresh(row)
            return _to_user(row)

    async def get_usage(self, *, user_id: UUID, usage_date: date) -> int:
        async with self._session_factory() as session:
            value = await session.get(DailyUsageRow, (user_id, usage_date))
            return value.question_count if value is not None else 0

    async def consume_question(
        self, *, user_id: UUID, usage_date: date, limit: int
    ) -> int | None:
        """原子占用一次额度；达到上限时返回 None。"""

        stmt = pg_insert(DailyUsageRow).values(
            user_id=user_id,
            usage_date=usage_date,
            question_count=1,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DailyUsageRow.user_id, DailyUsageRow.usage_date],
            set_={"question_count": DailyUsageRow.question_count + 1},
            where=DailyUsageRow.question_count < limit,
        ).returning(DailyUsageRow.question_count)
        async with self._session_factory() as session:
            used = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
            return int(used) if used is not None else None


class EphemeralAuthRepository:
    """无数据库测试/联调时使用的进程内认证仓库。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._users: dict[UUID, dict] = {}
        self._usernames: dict[str, UUID] = {}
        self._tokens: dict[str, tuple[UUID, datetime]] = {}
        self._usage: dict[tuple[UUID, date], int] = {}

    @staticmethod
    def _to_user(data: dict) -> UserAccount:
        return UserAccount(
            id=data["id"],
            username=data["username"],
            role=data["role"],
            status=data["status"],
            created_at=data["created_at"],
            approved_at=data.get("approved_at"),
        )

    async def create_user(
        self, *, username: str, password_hash: str
    ) -> UserAccount | None:
        async with self._lock:
            if username in self._usernames:
                return None
            user_id = uuid4()
            data = {
                "id": user_id,
                "username": username,
                "password_hash": password_hash,
                "role": "user",
                "status": "pending",
                "created_at": _utcnow(),
                "approved_at": None,
            }
            self._users[user_id] = data
            self._usernames[username] = user_id
            return self._to_user(data)

    async def get_credentials(self, *, username: str) -> tuple[UserAccount, str] | None:
        user_id = self._usernames.get(username)
        data = self._users.get(user_id) if user_id else None
        return (self._to_user(data), data["password_hash"]) if data else None

    async def get_user(self, *, user_id: UUID) -> UserAccount | None:
        data = self._users.get(user_id)
        return self._to_user(data) if data else None

    async def ensure_superuser(
        self, *, username: str, password_hash: str
    ) -> UserAccount:
        async with self._lock:
            existing_id = self._usernames.get(username)
            if existing_id is not None:
                data = self._users[existing_id]
                if data["role"] != "superuser":
                    raise ValueError(
                        "bootstrap superuser username is already registered"
                    )
                return self._to_user(data)
            user_id = uuid4()
            now = _utcnow()
            data = {
                "id": user_id,
                "username": username,
                "password_hash": password_hash,
                "role": "superuser",
                "status": "approved",
                "created_at": now,
                "approved_at": now,
            }
            self._users[user_id] = data
            self._usernames[username] = user_id
            return self._to_user(data)

    async def create_auth_session(
        self, *, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> None:
        async with self._lock:
            self._tokens[token_hash] = (user_id, expires_at)

    async def authenticate(self, *, token_hash: str) -> UserAccount | None:
        entry = self._tokens.get(token_hash)
        if entry is None or entry[1] <= _utcnow():
            return None
        data = self._users.get(entry[0])
        if data is None or data["status"] != "approved":
            return None
        return self._to_user(data)

    async def revoke(self, *, token_hash: str) -> None:
        async with self._lock:
            self._tokens.pop(token_hash, None)

    async def list_users(
        self, *, status: UserStatus | None = None
    ) -> list[UserAccount]:
        rows = [
            self._to_user(data)
            for data in self._users.values()
            if status is None or data["status"] == status
        ]
        return sorted(rows, key=lambda user: user.created_at, reverse=True)

    async def set_status(
        self, *, user_id: UUID, status: UserStatus, approved_by: UUID
    ) -> UserAccount | None:
        async with self._lock:
            data = self._users.get(user_id)
            if data is None or data["role"] == "superuser":
                return None
            data["status"] = status
            data["approved_by"] = approved_by
            data["approved_at"] = _utcnow() if status == "approved" else None
            if status != "approved":
                self._tokens = {
                    token: entry
                    for token, entry in self._tokens.items()
                    if entry[0] != user_id
                }
            return self._to_user(data)

    async def get_usage(self, *, user_id: UUID, usage_date: date) -> int:
        return self._usage.get((user_id, usage_date), 0)

    async def consume_question(
        self, *, user_id: UUID, usage_date: date, limit: int
    ) -> int | None:
        async with self._lock:
            key = (user_id, usage_date)
            used = self._usage.get(key, 0)
            if used >= limit:
                return None
            used += 1
            self._usage[key] = used
            return used
