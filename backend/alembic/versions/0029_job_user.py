"""A job remembers who started it: the jobs that exist were the admin's

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-12 16:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    op.execute("UPDATE jobs SET user_id = (SELECT min(id) FROM users WHERE role = 'admin')")
    op.create_index('ix_jobs_user_id', 'jobs', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_jobs_user_id', table_name='jobs')
    op.drop_column('jobs', 'user_id')
