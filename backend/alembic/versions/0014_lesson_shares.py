"""Lesson share links (read-only or editable, no sign-in)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-01 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'lesson_shares',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('lesson_id', sa.Integer(), sa.ForeignKey('lessons.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mode', sa.String(5), nullable=False),
        sa.Column('token', sa.String(64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('lesson_id', 'mode', name='ux_lesson_shares_mode'),
    )
    op.create_index('ix_lesson_shares_lesson_id', 'lesson_shares', ['lesson_id'])


def downgrade() -> None:
    op.drop_index('ix_lesson_shares_lesson_id', table_name='lesson_shares')
    op.drop_table('lesson_shares')
