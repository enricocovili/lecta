"""Database models (SQLAlchemy 2, typed declarative mappings)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --------------------------------------------------------------------------- auth


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = _now()
    password_changed_at: Mapped[datetime] = _now()


class Passkey(Base):
    """A WebAuthn credential of the admin: signing in with it (fingerprint, PIN, a password manager such as Bitwarden) needs no password."""

    __tablename__ = "passkeys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[str] = mapped_column(String(1024), unique=True)  # base64url
    public_key: Mapped[bytes] = mapped_column(LargeBinary)  # COSE
    sign_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    transports: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    synced: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # a multi-device (synced) passkey
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"

    # sha256 of the cookie token; the raw token is never stored.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id", ondelete="CASCADE"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------- content


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    academic_year: Mapped[str | None] = mapped_column(String(20))
    language: Mapped[str] = mapped_column(String(16), default="it", server_default="it")
    tags: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    description: Mapped[str | None] = mapped_column(Text)
    preamble_override: Mapped[str | None] = mapped_column(Text)
    engine: Mapped[str | None] = mapped_column(String(16))  # None = global default
    # The student's guidelines for writing this subject's text; empty = the AI decides.
    guidelines: Mapped[str | None] = mapped_column(Text)
    published: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    current_publication_id: Mapped[int | None] = mapped_column(Integer)
    # The course's updated_at that the latest publish job built (automatic republish compares with it).
    publish_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    chapters: Mapped[list[Chapter]] = relationship(
        back_populates="course", order_by="Chapter.position", cascade="all, delete-orphan"
    )


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("course_id", "slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    slug: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(String(300))  # chapters/NN-slug.tex
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    course: Mapped[Course] = relationship(back_populates="chapters")


class ProjectFile(Base):
    """Current state of one file of a course's LaTeX project."""

    __tablename__ = "project_files"
    __table_args__ = (
        UniqueConstraint("course_id", "path"),
        Index("ix_project_files_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(300))
    blob: Mapped[str] = mapped_column(String(64))  # sha256 hex in the blob store
    size: Mapped[int] = mapped_column(BigInteger)
    is_text: Mapped[bool] = mapped_column(Boolean)
    text_content: Mapped[str | None] = mapped_column(Text)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=True)
    # e.g. {"origin": "source", "source_file_id": 12} for images cropped from uploads
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------- lessons


class Lesson(Base):
    """A lecture worked on live: the slides (PDF) with the student's typed notes and pen strokes per page.
    The lesson is the input of the import: notes and annotated slides become the course text."""

    __tablename__ = "lessons"
    __table_args__ = (Index("ux_lessons_course_number", "course_id", "number", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)  # 1, 2, 3… within the course: the lesson's address is course/lessons/number
    title: Mapped[str] = mapped_column(String(300))
    # working | completed: completed once its text is merged into the course notes (a generation wrote it into a chapter);
    # it stays so until it is set back by hand.
    status: Mapped[str] = mapped_column(String(12), default="working", server_default="working")
    pdf_blob: Mapped[str | None] = mapped_column(String(64))  # the slides, in the blob store
    pdf_name: Mapped[str | None] = mapped_column(Text)
    pdf_pages: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The latest text generation: {job_id, at, chapters: [{course_id, chapter_id, title}]}
    last_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The chapter holding this lesson's text (its section is between «lezione N» markers): generating again updates it there.
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    # The page that was open last: the editor scrolls there when the lesson is opened again.
    last_page_id: Mapped[int | None] = mapped_column(ForeignKey("lesson_pages.id", ondelete="SET NULL", use_alter=True, name="fk_lessons_last_page"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class LessonPage(Base):
    """One page of a lesson: a slide of the PDF or a blank page added by hand."""

    __tablename__ = "lesson_pages"
    __table_args__ = (Index("ix_lesson_pages_order", "lesson_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # 1-based, in the lesson
    kind: Mapped[str] = mapped_column(String(10))  # slide | blank
    slide_page: Mapped[int | None] = mapped_column(Integer)  # 1-based page of the PDF (slides)
    ratio: Mapped[float] = mapped_column(Float, default=0.5625, server_default="0.5625")  # height / width
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")  # Markdown
    # [{t: pen|hl, c: "#rrggbb", w: stroke width / page width, p: [x, y, pressure, …]}], x and y in page widths
    ink: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    updated_at: Mapped[datetime] = _now()


class LessonShare(Base):
    """A link to a lesson that works without signing in: read-only, or with the right to edit notes and strokes.
    One link per mode and lesson; revoking is deleting the row."""

    __tablename__ = "lesson_shares"
    __table_args__ = (UniqueConstraint("lesson_id", "mode", name="ux_lesson_shares_mode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(5))  # read | write
    token: Mapped[str] = mapped_column(String(64), unique=True)  # in the link: /s/<token>
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- jobs


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "priority", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300), default="")
    # queued | running | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    progress_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobLog(Base):
    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = _now()
    level: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    # Structured details for filtering/grouping: stage, task, item, figure, attempt, kind, audit_id, …
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class JobStep(Base):
    """Memoised result of one step of a job, so re-runs resume where they stopped."""

    __tablename__ = "job_steps"
    __table_args__ = (UniqueConstraint("job_id", "key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(300))
    result: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------- publishing


class Publication(Base):
    """The public version of a course (clean build, review markers stripped); one per course, replaced on every republish."""

    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True, unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    pdf_blob: Mapped[str] = mapped_column(String(64))
    pdf_size: Mapped[int] = mapped_column(BigInteger)
    source_blob: Mapped[str | None] = mapped_column(String(64))  # zip of the published LaTeX project
    source_size: Mapped[int | None] = mapped_column(BigInteger)
    # [{slug, title, position, page_start, page_end, pdf_blob, pdf_size}]
    chapters: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = _now()


class Build(Base):
    """Result of one compile (draft, publish or proposal test build)."""

    __tablename__ = "builds"
    __table_args__ = (Index("ix_builds_course_kind", "course_id", "kind", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))  # draft | publish | proposal | figure
    status: Mapped[str] = mapped_column(String(20))  # ok | error | timeout | superseded
    engine: Mapped[str] = mapped_column(String(16))
    includeonly: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    diagnostics: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    figures: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    log: Mapped[str] = mapped_column(Text, default="")
    pdf_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------- AI providers


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(30))  # anthropic | openai | gemini | openai_compat | fake
    base_url: Mapped[str | None] = mapped_column(Text)
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    # [{id, label, input_per_mtok, output_per_mtok, vision}]
    models: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_test: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Prompt(Base):
    """Versioned system prompts. The active version of each key is used; shipped
    defaults live in code (services/prompts.py) and apply when no row is active."""

    __tablename__ = "prompts"
    __table_args__ = (UniqueConstraint("key", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class AuditLog(Base):
    """One call to an AI provider (for the cost overview and the job logs; payloads aren't stored)."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_ts", "ts"), Index("ix_audit_job_key", "job_id", "request_key"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = _now()
    provider_id: Mapped[int | None] = mapped_column(Integer)
    provider_name: Mapped[str] = mapped_column(String(100))
    provider_type: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(40))
    task: Mapped[str | None] = mapped_column(String(80))
    request_key: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))  # sending | ok | error
    error: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    chat_session_id: Mapped[int | None] = mapped_column(Integer)


# --------------------------------------------------------------------------- uploads & sources


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="receiving")  # receiving | queued | done | failed
    target_course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    target_chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    total_size: Mapped[int] = mapped_column(BigInteger, default=0)
    via: Mapped[str] = mapped_column(String(20), default="web")  # lesson; web | quick: older uploads of loose files
    created_at: Mapped[datetime] = _now()


class SourceFile(Base):
    """An uploaded file (or a member of an uploaded zip). Originals are kept privately."""

    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(Integer)  # the zip it came from
    name: Mapped[str] = mapped_column(Text)  # original file name (untrusted hint)
    folder: Mapped[str | None] = mapped_column(Text)  # folder inside the zip / relative path (untrusted hint)
    kind: Mapped[str] = mapped_column(String(20))  # pdf | image | markdown | text | zip | unsupported | junk
    mime: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    blob: Mapped[str | None] = mapped_column(String(64))
    pages: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | skipped | unsupported | error
    reason: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = _now()


class SourceLink(Base):
    """Which course/chapter a source file contributed to."""

    __tablename__ = "source_links"
    __table_args__ = (UniqueConstraint("source_file_id", "course_id", "chapter_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    job_id: Mapped[int | None] = mapped_column(BigInteger)  # the import that added it
    created_at: Mapped[datetime] = _now()


class IngestItem(Base):
    """One entry of an ingestion manifest: a page of a PDF, a photo, a Markdown file…"""

    __tablename__ = "ingest_items"
    __table_args__ = (UniqueConstraint("job_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(BigInteger, index=True)
    upload_id: Mapped[int] = mapped_column(Integer, index=True)
    source_file_id: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)  # 1-based
    key: Mapped[str] = mapped_column(String(40))  # i1, i2, ...
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # slide | annotated_slide | handwritten | typed | photo | markdown | text | unknown
    kind_source: Mapped[str] = mapped_column(String(10), default="local")  # local | vision
    label: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str | None] = mapped_column(Text)  # text layer / transcription
    latex: Mapped[str | None] = mapped_column(Text)  # markdown → LaTeX (pandoc)
    annotations: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    image_blob: Mapped[str | None] = mapped_column(String(64))  # model input (≈150 DPI)
    preview_blob: Mapped[str | None] = mapped_column(String(64))  # thumbnail
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(8))
    group_key: Mapped[str | None] = mapped_column(String(40))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class IngestFigure(Base):
    """A picture found in the sources (embedded image, vector drawing, drawing seen by the model, Markdown image)."""

    __tablename__ = "ingest_figures"
    __table_args__ = (UniqueConstraint("job_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(BigInteger, index=True)
    item_id: Mapped[int | None] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(60))  # temporary id used in generated LaTeX (figj12n3)
    origin: Mapped[str] = mapped_column(String(20))  # image | vector | drawing | md_image
    source_ref: Mapped[str] = mapped_column(Text)  # provenance ref of the original
    crop_blob: Mapped[str | None] = mapped_column(String(64))
    code_text: Mapped[str | None] = mapped_column(Text)  # mermaid / ascii source
    bbox: Mapped[list[Any] | None] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    description_ref: Mapped[str | None] = mapped_column(Text)  # provenance of the description (model output)
    classification: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | used | appended | dropped
    code: Mapped[str | None] = mapped_column(Text)  # final TikZ/pgfplots code
    latex_inline: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    render_blob: Mapped[str | None] = mapped_column(String(64))
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    log: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    group_key: Mapped[str | None] = mapped_column(String(40))
    path: Mapped[str | None] = mapped_column(Text)  # images/… in the course, once written


# --------------------------------------------------------------------------- retrieval index

from pgvector.sqlalchemy import Vector  # noqa: E402


class IndexChunk(Base):
    """~100-token windows of chapter text (LaTeX stripped), for hybrid retrieval."""

    __tablename__ = "index_chunks"
    __table_args__ = (Index("ix_index_chunks_tsv", "tsv", postgresql_using="gin"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    heading: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=True)
    embedding: Mapped[Any] = mapped_column(Vector(384), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = _now()


class IndexState(Base):
    """What was indexed for each chapter (to re-index only when the file changed)."""

    __tablename__ = "index_state"

    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True)
    blob: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(200))
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------- review chat


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    # user: ready | sent | error ; assistant: streaming | done | error
    status: Mapped[str] = mapped_column(String(20), default="ready")
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    request: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # the exact EgressRequest (user messages)
    # assistant: what the turn changed {status: applied|undone, files: [...], chapters: [...], pre/post snapshots for undo}
    change: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # assistant: the tool calls of the turn [{id, name, label, status, summary}]
    steps: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    suggestions: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # document feedback (mode "review")
    reply_to: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = _now()
