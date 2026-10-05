"""The assistant can work on a laboratory: its conversations belong to the lab

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-05 15:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('chat_sessions', sa.Column('lab_id', sa.Integer(), sa.ForeignKey('labs.id', ondelete='CASCADE'), nullable=True))
    op.create_index('ix_chat_sessions_lab_id', 'chat_sessions', ['lab_id'])


def downgrade() -> None:
    op.drop_index('ix_chat_sessions_lab_id', table_name='chat_sessions')
    op.drop_column('chat_sessions', 'lab_id')
