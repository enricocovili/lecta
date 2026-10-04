"""Source files no longer keep a folder or the zip they came from: a lesson hands the import flat files

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-04 18:00:00

Members of zips uploaded before lessons stay as plain source files of their upload.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('source_files', 'parent_id')
    op.drop_column('source_files', 'folder')


def downgrade() -> None:
    op.add_column('source_files', sa.Column('folder', sa.Text(), nullable=True))
    op.add_column('source_files', sa.Column('parent_id', sa.Integer(), nullable=True))
