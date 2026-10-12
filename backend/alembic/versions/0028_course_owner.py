"""Every course has an owner: the courses that exist become the admin's

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-12 15:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('courses', sa.Column('owner_id', sa.Integer(), nullable=True))
    op.execute("UPDATE courses SET owner_id = (SELECT min(id) FROM users WHERE role = 'admin')")
    op.alter_column('courses', 'owner_id', nullable=False)
    op.create_foreign_key('courses_owner_id_fkey', 'courses', 'users', ['owner_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_courses_owner_id', 'courses', ['owner_id'])


def downgrade() -> None:
    op.drop_index('ix_courses_owner_id', table_name='courses')
    op.drop_constraint('courses_owner_id_fkey', 'courses', type_='foreignkey')
    op.drop_column('courses', 'owner_id')
