"""Lessons: slides with typed notes and pen strokes per page, and the course's writing guidelines

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-30 12:00:00

* lessons / lesson_pages: the live-notes workspace (slides PDF, Markdown notes and ink per page).
* courses.guidelines: what the student wants from the text of the subject (empty = the AI decides).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('courses', sa.Column('guidelines', sa.Text(), nullable=True))
    op.create_table(
        'lessons',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('pdf_blob', sa.String(64), nullable=True),
        sa.Column('pdf_name', sa.Text(), nullable=True),
        sa.Column('pdf_pages', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_result', postgresql.JSONB(), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_lessons_course_id', 'lessons', ['course_id'])
    op.create_table(
        'lesson_pages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('lesson_id', sa.Integer(), sa.ForeignKey('lessons.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('slide_page', sa.Integer(), nullable=True),
        sa.Column('ratio', sa.Float(), server_default='0.5625', nullable=False),
        sa.Column('notes', sa.Text(), server_default='', nullable=False),
        sa.Column('ink', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_lesson_pages_order', 'lesson_pages', ['lesson_id', 'position'])


def downgrade() -> None:
    op.drop_table('lesson_pages')
    op.drop_table('lessons')
    op.drop_column('courses', 'guidelines')
