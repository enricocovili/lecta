"""A conversation with the assistant belongs to whoever started it, and every AI call records its user

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-12 17:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None

ADMIN = "(SELECT min(id) FROM users WHERE role = 'admin')"


def upgrade() -> None:
    op.add_column('chat_sessions', sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True))
    op.execute(f"UPDATE chat_sessions SET user_id = {ADMIN}")
    op.execute("DELETE FROM chat_sessions WHERE user_id IS NULL")  # only without any admin: nobody's conversations
    op.alter_column('chat_sessions', 'user_id', nullable=False)
    op.create_index('ix_chat_sessions_user_id', 'chat_sessions', ['user_id'])
    op.add_column('audit_log', sa.Column('user_id', sa.Integer(), nullable=True))
    op.execute(f"UPDATE audit_log SET user_id = {ADMIN}")


def downgrade() -> None:
    op.drop_column('audit_log', 'user_id')
    op.drop_index('ix_chat_sessions_user_id', table_name='chat_sessions')
    op.drop_column('chat_sessions', 'user_id')
