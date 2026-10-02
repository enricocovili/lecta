"""AI workspace: the assistant works with tools and its steps are kept; publications keep the LaTeX source

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-29 12:00:00

* chat_messages.steps: what the assistant did in a turn (tool calls) for the activity timeline.
* chat_messages.change: new shape (applied/undone, with the snapshot needed to undo); changes proposed in the
  old format that were never accepted are dropped.
* publications.source_blob / source_size: the published LaTeX project as a zip (public source download).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('chat_messages', sa.Column('steps', postgresql.JSONB(), server_default='[]', nullable=False))
    op.add_column('chat_messages', sa.Column('suggestions', postgresql.JSONB(), server_default='[]', nullable=False))
    op.add_column('chat_messages', sa.Column('review', postgresql.JSONB(), nullable=True))
    op.add_column('publications', sa.Column('source_blob', sa.String(64), nullable=True))
    op.add_column('publications', sa.Column('source_size', sa.BigInteger(), nullable=True))
    # Proposals of the old chat (a diff waiting for "Accetta") can't be shown any more.
    op.execute("UPDATE chat_messages SET change = NULL WHERE change IS NOT NULL AND change->>'status' IN ('pending', 'discarded', 'empty')")
    op.execute("UPDATE chat_messages SET status = 'error', error = 'interrotto da un aggiornamento' WHERE status = 'streaming'")
    op.execute("DELETE FROM prompts WHERE key = 'chat.review'")


def downgrade() -> None:
    op.drop_column('publications', 'source_size')
    op.drop_column('publications', 'source_blob')
    op.drop_column('chat_messages', 'review')
    op.drop_column('chat_messages', 'suggestions')
    op.drop_column('chat_messages', 'steps')
