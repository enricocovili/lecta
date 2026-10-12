"""The single admin account becomes a table of users, each with a role and a disabled flag

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-12 14:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The foreign keys of passkeys and sessions follow the renamed table.
    op.rename_table('admin_user', 'users')
    op.execute('ALTER SEQUENCE admin_user_id_seq RENAME TO users_id_seq')
    op.execute('ALTER TABLE users RENAME CONSTRAINT admin_user_pkey TO users_pkey')
    op.execute('ALTER TABLE users RENAME CONSTRAINT admin_user_username_key TO users_username_key')
    # The account that exists is the admin; new users always get their role explicitly.
    op.add_column('users', sa.Column('role', sa.String(16), nullable=False, server_default='admin'))
    op.alter_column('users', 'role', server_default=None)
    op.create_check_constraint('ck_users_role', 'users', "role IN ('viewer', 'editor', 'admin')")
    op.add_column('users', sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('users', 'password_hash', nullable=True)


def downgrade() -> None:
    # Only the password accounts can go back to the single-admin table.
    op.execute('DELETE FROM users WHERE password_hash IS NULL')
    op.alter_column('users', 'password_hash', nullable=False)
    op.drop_column('users', 'disabled_at')
    op.drop_constraint('ck_users_role', 'users', type_='check')
    op.drop_column('users', 'role')
    op.execute('ALTER TABLE users RENAME CONSTRAINT users_username_key TO admin_user_username_key')
    op.execute('ALTER TABLE users RENAME CONSTRAINT users_pkey TO admin_user_pkey')
    op.execute('ALTER SEQUENCE users_id_seq RENAME TO admin_user_id_seq')
    op.rename_table('users', 'admin_user')
