"""«Non capita» marks and the last open page of a lesson are each user's (#87); the existing ones go to the course's owner

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-15 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'lesson_marks',
        sa.Column('page_id', sa.Integer(), sa.ForeignKey('lesson_pages.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    )
    op.create_index('ix_lesson_marks_user_id', 'lesson_marks', ['user_id'])
    op.create_table(
        'lesson_places',
        sa.Column('lesson_id', sa.Integer(), sa.ForeignKey('lessons.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('page_id', sa.Integer(), sa.ForeignKey('lesson_pages.id', ondelete='CASCADE'), nullable=False),
    )
    op.create_index('ix_lesson_places_user_id', 'lesson_places', ['user_id'])
    op.execute("""
        INSERT INTO lesson_marks (page_id, user_id)
        SELECT p.id, c.owner_id FROM lesson_pages p JOIN lessons l ON l.id = p.lesson_id JOIN courses c ON c.id = l.course_id
        WHERE p.unclear
    """)
    op.execute("""
        INSERT INTO lesson_places (lesson_id, user_id, page_id)
        SELECT l.id, c.owner_id, l.last_page_id FROM lessons l JOIN courses c ON c.id = l.course_id
        WHERE l.last_page_id IS NOT NULL
    """)
    op.drop_column('lesson_pages', 'unclear')
    op.drop_column('lessons', 'last_page_id')


def downgrade() -> None:
    op.add_column('lessons', sa.Column('last_page_id', sa.Integer(), sa.ForeignKey('lesson_pages.id', ondelete='SET NULL'), nullable=True))
    op.add_column('lesson_pages', sa.Column('unclear', sa.Boolean(), nullable=False, server_default='false'))
    op.execute("""
        UPDATE lessons l SET last_page_id = pl.page_id FROM lesson_places pl, courses c
        WHERE pl.lesson_id = l.id AND c.id = l.course_id AND pl.user_id = c.owner_id
    """)
    op.execute("""
        UPDATE lesson_pages p SET unclear = true FROM lesson_marks m, lessons l, courses c
        WHERE m.page_id = p.id AND l.id = p.lesson_id AND c.id = l.course_id AND m.user_id = c.owner_id
    """)
    op.drop_table('lesson_places')
    op.drop_table('lesson_marks')
