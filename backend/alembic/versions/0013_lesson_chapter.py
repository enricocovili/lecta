"""A lesson remembers the chapter its text lives in

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-30 20:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('lessons', sa.Column('chapter_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_lessons_chapter', 'lessons', 'chapters', ['chapter_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_lessons_chapter', 'lessons', type_='foreignkey')
    op.drop_column('lessons', 'chapter_id')
