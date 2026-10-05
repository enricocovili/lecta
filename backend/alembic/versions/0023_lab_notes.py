"""A laboratory has free notes, not tied to a file

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-05 13:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('labs', sa.Column('notes', sa.Text(), server_default='', nullable=False))
    op.add_column('labs', sa.Column('notes_version', sa.Integer(), server_default='1', nullable=False))


def downgrade() -> None:
    op.drop_column('labs', 'notes_version')
    op.drop_column('labs', 'notes')
