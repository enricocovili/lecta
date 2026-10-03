"""A lesson is either being worked on or completed (its text is merged into the course notes)

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-03 10:00:00

Lessons whose text was already written into a chapter start as completed.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('lessons', sa.Column('status', sa.String(length=12), nullable=False, server_default='working'))
    op.execute("UPDATE lessons SET status = 'completed' WHERE chapter_id IS NOT NULL AND generated_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column('lessons', 'status')
