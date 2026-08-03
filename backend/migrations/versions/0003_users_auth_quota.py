"""add users, approval, auth sessions, daily quota and session ownership

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="user", nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="pending", nullable=False
        ),
        sa.Column(
            "approved_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('superuser', 'user')", name="ck_users_role"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_users_status",
        ),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index(
        "ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"], unique=True
    )

    op.create_table(
        "daily_question_usage",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("usage_date", sa.Date(), primary_key=True),
        sa.Column("question_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("question_count >= 0", name="ck_daily_usage_nonnegative"),
    )

    op.add_column("chat_sessions", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_chat_sessions_owner_user_id",
        "chat_sessions",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_chat_sessions_owner_user_id", "chat_sessions", ["owner_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_owner_user_id", table_name="chat_sessions")
    op.drop_constraint(
        "fk_chat_sessions_owner_user_id", "chat_sessions", type_="foreignkey"
    )
    op.drop_column("chat_sessions", "owner_user_id")
    op.drop_table("daily_question_usage")
    op.drop_table("auth_sessions")
    op.drop_table("users")
