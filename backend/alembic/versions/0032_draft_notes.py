"""Draft notes: comments and suggestions that readers leave on a block of a published course

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-12 20:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'draft_notes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('chapter_id', sa.Integer(), sa.ForeignKey('chapters.id', ondelete='CASCADE'), nullable=False),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('src', sa.Text(), nullable=False),
        sa.Column('start', sa.Integer(), nullable=False),
        sa.Column('end', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False, server_default=''),
        sa.Column('proposed', sa.Text(), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='open'),
        sa.Column('resolved_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('comment', 'suggestion')", name='ck_draft_notes_kind'),
        sa.CheckConstraint("status IN ('open', 'accepted', 'rejected', 'resolved')", name='ck_draft_notes_status'),
        sa.CheckConstraint("kind = 'comment' OR proposed IS NOT NULL", name='ck_draft_notes_proposed'),
    )
    op.create_index('ix_draft_notes_chapter', 'draft_notes', ['chapter_id', 'status'])
    op.create_index('ix_draft_notes_course', 'draft_notes', ['course_id', 'status'])
    op.create_index('ix_draft_notes_author_id', 'draft_notes', ['author_id'])


def downgrade() -> None:
    op.drop_table('draft_notes')
