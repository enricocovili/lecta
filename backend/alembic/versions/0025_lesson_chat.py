"""The assistant can be asked about a lesson's slides: its conversations belong to the lesson

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-09 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('chat_sessions', sa.Column('lesson_id', sa.Integer(), sa.ForeignKey('lessons.id', ondelete='CASCADE'), nullable=True))
    op.create_index('ix_chat_sessions_lesson_id', 'chat_sessions', ['lesson_id'])


def downgrade() -> None:
    op.drop_index('ix_chat_sessions_lesson_id', table_name='chat_sessions')
    op.drop_column('chat_sessions', 'lesson_id')
