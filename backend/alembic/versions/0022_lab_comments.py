"""Comments on the lines of a lab's files, written live

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-05 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'lab_comments',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('lab_id', sa.Integer(), sa.ForeignKey('labs.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('file_id', sa.Integer(), sa.ForeignKey('lab_files.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('anchor', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('body', sa.Text(), server_default='', nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('lab_comments')
