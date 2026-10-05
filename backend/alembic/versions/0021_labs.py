"""A lesson can have a laboratory with its files

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-05 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'labs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('lesson_id', sa.Integer(), sa.ForeignKey('lessons.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'lab_files',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('lab_id', sa.Integer(), sa.ForeignKey('labs.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('path', sa.String(length=300), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('language', sa.String(length=30), nullable=True),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('blob', sa.String(length=64), nullable=True),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('lab_id', 'path', name='ux_lab_files_path'),
    )


def downgrade() -> None:
    op.drop_table('lab_files')
    op.drop_table('labs')
