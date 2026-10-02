"""The app is now called Lecta: a site title still at the old default follows

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-02 09:00:00
"""
from __future__ import annotations

from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only the untouched default moves; a title the admin chose is left alone.
    op.execute("""
        UPDATE settings SET value = jsonb_set(value, '{title}', '"Lecta"')
        WHERE key = 'site' AND value->>'title' = 'Appunti'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE settings SET value = jsonb_set(value, '{title}', '"Appunti"')
        WHERE key = 'site' AND value->>'title' = 'Lecta'
    """)
