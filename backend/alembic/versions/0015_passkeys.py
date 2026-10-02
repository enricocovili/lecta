"""Passkeys (WebAuthn) to sign in without a password

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-01 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'passkeys',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('admin_user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('credential_id', sa.String(1024), nullable=False, unique=True),
        sa.Column('public_key', sa.LargeBinary(), nullable=False),
        sa.Column('sign_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('name', sa.String(100), server_default='', nullable=False),
        sa.Column('transports', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('synced', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_passkeys_user_id', 'passkeys', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_passkeys_user_id', table_name='passkeys')
    op.drop_table('passkeys')
