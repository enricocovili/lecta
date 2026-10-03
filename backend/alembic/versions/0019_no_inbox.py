"""No more «Da smistare»: the material of a lesson always goes into its course

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-04 00:30:00

Open items still waiting in the inbox are dropped with the table (counted in the log).
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    n = op.get_bind().execute(sa.text("SELECT count(*) FROM inbox_items WHERE status = 'open'")).scalar_one()
    if n:
        log.warning("0019: %s open inbox item(s) are dropped", n)
    op.drop_index('ix_inbox_status', table_name='inbox_items')
    op.drop_table('inbox_items')


def downgrade() -> None:
    op.create_table('inbox_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('language', sa.String(length=8), nullable=True),
    sa.Column('job_id', sa.BigInteger(), nullable=True),
    sa.Column('bundle', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('guesses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('source_file_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('assigned_job_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_inbox_status', 'inbox_items', ['status', 'id'], unique=False)
