"""A page of a lesson can be marked «non capita», to be explained later

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-12 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('lesson_pages', sa.Column('unclear', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('lesson_pages', 'unclear')
