"""Invite links: an admin creates one with a role, whoever opens it creates an account with a passkey

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-12 18:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'invites',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('used_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.CheckConstraint("role IN ('viewer', 'editor', 'admin')", name='ck_invites_role'),
    )


def downgrade() -> None:
    op.drop_table('invites')
