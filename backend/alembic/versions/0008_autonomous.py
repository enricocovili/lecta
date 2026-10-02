"""autonomous platform: no approvals, reviews, revisions or privacy gate; publishing is ON/OFF

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25 12:00:00

Drops approvals, session grants, proposals and the revision history; provider ZDR/local flags;
audit payload columns; per-chapter publication flags; keeps one publication per course.
Pending proposals and open inbox items in the old format can't be carried over: they are counted
in the log and removed.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

REMOVED_PROMPTS = ("vision.page", "handwriting.transcribe", "vision.classify", "grouping.align", "notes.write", "notes.merge",
                   "diagram.generate", "diagram.compare")


def _count(conn, sql: str) -> int:
    return int(conn.execute(sa.text(sql)).scalar() or 0)


def upgrade() -> None:
    conn = op.get_bind()
    pending = _count(conn, "SELECT count(*) FROM proposals WHERE status IN ('pending', 'testing')")
    inbox = _count(conn, "SELECT count(*) FROM inbox_items WHERE status = 'open'")
    hidden = _count(conn, "SELECT count(*) FROM chapters ch JOIN courses c ON c.id = ch.course_id WHERE c.published AND NOT ch.publishable")
    if pending:
        log.warning("0008: %s pending AI proposal(s) are removed (the review queue no longer exists)", pending)
    if inbox:
        log.warning("0008: %s open inbox item(s) in the old format are discarded", inbox)
    if hidden:
        log.warning("0008: %s chapter(s) excluded from publication become public at the next republish", hidden)

    # Jobs and chat messages that waited for an approval.
    op.execute("UPDATE jobs SET status = 'cancelled', finished_at = now(), error = 'approvals were removed; start the import again' "
               "WHERE status = 'awaiting_approval'")
    op.execute("UPDATE jobs SET status = 'cancelled', finished_at = now(), error = 'this kind of job no longer exists' "
               "WHERE kind IN ('figure.regenerate') AND status IN ('queued', 'running')")
    op.execute("UPDATE chat_messages SET status = 'error', error = 'approvals were removed' WHERE status = 'pending_approval'")
    op.execute("UPDATE inbox_items SET status = 'discarded', decided_at = now() WHERE status = 'open'")

    op.drop_table('session_grants')
    op.drop_table('approvals')
    op.drop_table('proposal_files')
    op.drop_table('proposals')
    op.drop_table('revisions')

    for col in ('is_local', 'zdr', 'zdr_notes'):
        op.drop_column('providers', col)
    for col in ('is_local', 'payload_sha256', 'payload_path', 'response_sha256', 'approval_id', 'authorization'):
        op.drop_column('audit_log', col)
    op.drop_column('chat_messages', 'approval_id')
    op.drop_column('chat_messages', 'proposal_id')
    op.add_column('chat_messages', sa.Column('change', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.drop_column('source_links', 'proposal_id')
    op.add_column('source_links', sa.Column('job_id', sa.BigInteger(), nullable=True))
    op.drop_column('ingest_items', 'excluded')
    op.add_column('ingest_figures', sa.Column('path', sa.Text(), nullable=True))

    # Publishing: one publication per course, no per-chapter exclusion.
    op.drop_column('chapters', 'publishable')
    op.execute("DELETE FROM publications p WHERE NOT EXISTS (SELECT 1 FROM courses c WHERE c.current_publication_id = p.id)")
    op.drop_column('publications', 'revision_id')
    op.drop_column('publications', 'warnings')
    op.drop_index('ix_publications_course_id', table_name='publications')
    op.create_index('ix_publications_course_id', 'publications', ['course_id'], unique=True)
    op.add_column('courses', sa.Column('publish_requested_at', sa.DateTime(timezone=True), nullable=True))
    # Courses whose public PDF is already up to date don't need an automatic republish.
    op.execute("UPDATE courses c SET publish_requested_at = c.updated_at FROM publications p "
               "WHERE p.id = c.current_publication_id AND p.created_at >= c.updated_at")

    # Settings and prompts of removed features.
    op.execute("DELETE FROM settings WHERE key = 'privacy'")
    op.execute("UPDATE settings SET value = value - 'diagram' WHERE key = 'roles'")
    op.execute("UPDATE settings SET value = value - 'diagram_iterations' WHERE key = 'latex'")
    op.execute("DELETE FROM prompts WHERE key IN (" + ", ".join(f"'{k}'" for k in REMOVED_PROMPTS) + ")")


def downgrade() -> None:
    raise NotImplementedError("0008 removes whole features (approvals, reviews, revisions); restore a backup instead")
