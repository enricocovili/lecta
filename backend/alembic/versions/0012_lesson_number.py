"""Lessons are numbered within their course (course/lessons/<n> in the URL)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-30 18:30:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('lessons', sa.Column('number', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE lessons SET number = t.n FROM "
        "(SELECT id, row_number() OVER (PARTITION BY course_id ORDER BY id) AS n FROM lessons) t WHERE lessons.id = t.id"
    )
    op.alter_column('lessons', 'number', nullable=False)
    op.create_index('ux_lessons_course_number', 'lessons', ['course_id', 'number'], unique=True)


def downgrade() -> None:
    op.drop_index('ux_lessons_course_number', table_name='lessons')
    op.drop_column('lessons', 'number')
