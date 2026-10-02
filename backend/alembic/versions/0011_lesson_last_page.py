"""Lessons remember the last page that was open

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-30 18:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('lessons', sa.Column('last_page_id', sa.Integer(), sa.ForeignKey('lesson_pages.id', ondelete='SET NULL'), nullable=True))


def downgrade() -> None:
    op.drop_column('lessons', 'last_page_id')
