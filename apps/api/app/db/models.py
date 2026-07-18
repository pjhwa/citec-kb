"""Canonical schema for CI-TEC Knowledge Platform (PR-02).

Tables follow design § data model (Phase 0–1 core + forward stubs).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# BGE-M3 dense dimension (default). Re-embed if model changes.
EMBEDDING_DIM = 1024


def _uuid() -> str:
    return str(uuid4())


class Source(Base):
    """Ingestion source registry (fs_raw, jira, confluence, …)."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)  # fs_raw | jira | confluence
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    documents: Mapped[list[Document]] = relationship(back_populates="source")


class Document(Base):
    """Canonical normalized document (SoR)."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_type", "external_id", name="uq_documents_source_external"),
        Index("ix_documents_source_type", "source_type"),
        Index("ix_documents_status", "status"),
        Index("ix_documents_updated_at", "updated_at"),
        Index("ix_documents_metadata_gin", "metadata", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    source_id: Mapped[Optional[str]] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # support_history | tech_repo | checkitem | tuning_ai | insight | …
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False, server_default="")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="active"
    )  # active | archived | superseded | draft_insight
    source_uri: Mapped[Optional[str]] = mapped_column(String(2048))
    lang: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ko")
    # evidence grade: A | A- | B | draft
    evidence_grade: Mapped[str] = mapped_column(String(16), nullable=False, server_default="B")
    # taxonomy denormalized for filters
    environment: Mapped[Optional[str]] = mapped_column(String(32))  # csp | msp | onprem | hybrid
    domain: Mapped[Optional[str]] = mapped_column(String(64))
    work_type: Mapped[Optional[str]] = mapped_column(String(64))
    path_l2: Mapped[Optional[str]] = mapped_column(String(512))
    path_l3: Mapped[Optional[str]] = mapped_column(String(512))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    source: Mapped[Optional[Source]] = relationship(back_populates="documents")
    sections: Mapped[list[DocumentSection]] = relationship(back_populates="document")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="document")


class DocumentSection(Base):
    """Parent section for small-to-big retrieval."""

    __tablename__ = "document_sections"
    __table_args__ = (Index("ix_document_sections_document_id", "document_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    heading_path: Mapped[str] = mapped_column(String(2048), nullable=False, server_default="")
    level: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    document: Mapped[Document] = relationship(back_populates="sections")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="section")


class Chunk(Base):
    """Search unit with contextual header + FTS."""

    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_document_id", "document_id"),
        Index("ix_chunks_section_id", "section_id"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("document_sections.id", ondelete="SET NULL")
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    header_context: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tsv: Mapped[Optional[Any]] = mapped_column(TSVECTOR)
    # soft-delete when rechunking
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="chunks")
    section: Mapped[Optional[DocumentSection]] = relationship(back_populates="chunks")
    embedding: Mapped[Optional[Embedding]] = relationship(
        back_populates="chunk", uselist=False
    )


class Embedding(Base):
    """Dense vector for a chunk (model-versioned)."""

    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "model", name="uq_embeddings_chunk_model"),
        Index(
            "ix_embeddings_vector_hnsw",
            "vector",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"vector": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False, server_default="BAAI/bge-m3")
    dim: Mapped[int] = mapped_column(Integer, nullable=False, server_default=str(EMBEDDING_DIM))
    vector: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chunk: Mapped[Chunk] = relationship(back_populates="embedding")


class IngestJob(Base):
    """Batch / incremental ingest tracking."""

    __tablename__ = "ingest_jobs"
    __table_args__ = (Index("ix_ingest_jobs_source_started", "source_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    source_id: Mapped[Optional[str]] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    mode: Mapped[str] = mapped_column(String(32), nullable=False)  # full | incremental | reembed
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )  # pending | running | success | failed
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Checkitem(Base):
    """Normalized PISA diagnostic check item (1 row = 1 item)."""

    __tablename__ = "checkitems"
    __table_args__ = (
        UniqueConstraint("code", "lang", name="uq_checkitems_code_lang"),
        Index("ix_checkitems_area", "area"),
        Index("ix_checkitems_category", "category_1"),
        Index("ix_checkitems_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    lang: Mapped[str] = mapped_column(String(8), nullable=False, server_default="KO")
    area: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    category_1: Mapped[Optional[str]] = mapped_column(String(128))
    subcategory: Mapped[Optional[str]] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")
    check_method: Mapped[Optional[str]] = mapped_column(Text)
    check_criteria: Mapped[Optional[str]] = mapped_column(Text)
    check_result: Mapped[Optional[str]] = mapped_column(Text)
    risk_if_vulnerable: Mapped[Optional[str]] = mapped_column(Text)
    remediation: Mapped[Optional[str]] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tsv: Mapped[Optional[Any]] = mapped_column(TSVECTOR)
    document_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Entity(Base):
    """Business system / customer entity (e.g. monimo)."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. sys:monimo
    canonical_name: Mapped[str] = mapped_column(String(256), nullable=False)
    type: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="business_system"
    )
    customer: Mapped[Optional[str]] = mapped_column(String(256))
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    host_patterns: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    env_hints: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentEntity(Base):
    __tablename__ = "document_entities"
    __table_args__ = (
        UniqueConstraint("document_id", "entity_id", name="uq_document_entities"),
        Index("ix_document_entities_entity_id", "entity_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")


class LexiconTerm(Base):
    """Technical synonym dictionary (GRO, etc.)."""

    __tablename__ = "lexicon_terms"
    __table_args__ = (UniqueConstraint("canonical", name="uq_lexicon_canonical"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical: Mapped[str] = mapped_column(String(256), nullable=False)
    variants: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class IssueFrame(Base):
    """Structured ticket slots for similar-incident / prevention."""

    __tablename__ = "issue_frames"
    __table_args__ = (Index("ix_issue_frames_document_id", "document_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    symptom: Mapped[Optional[str]] = mapped_column(Text)
    root_cause: Mapped[Optional[str]] = mapped_column(Text)
    resolution: Mapped[Optional[str]] = mapped_column(Text)
    workaround: Mapped[Optional[str]] = mapped_column(Text)
    components: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    environment: Mapped[Optional[str]] = mapped_column(String(32))
    commands: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    quality: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    raw_extract: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CapacityRule(Base):
    """Diagnostic capacity standard (e.g. 1-week field units)."""

    __tablename__ = "capacity_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    basis: Mapped[str] = mapped_column(String(64), nullable=False, server_default="1안")
    period_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="7")
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="host"
    )  # host | instance
    mm_per_field_week: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.25")
    source_ref: Mapped[Optional[str]] = mapped_column(String(512))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class PricingRule(Base):
    __tablename__ = "pricing_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    field_group: Mapped[str] = mapped_column(String(64), nullable=False)
    unit_kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="host")
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default="KRW")
    source_ref: Mapped[Optional[str]] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class QueryLog(Base):
    __tablename__ = "queries"
    __table_args__ = (Index("ix_queries_created_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(String(128))
    query: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    mode: Mapped[Optional[str]] = mapped_column(String(64))
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    query_id: Mapped[str] = mapped_column(
        ForeignKey("queries.id", ondelete="CASCADE"), nullable=False
    )
    answer_md: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    citations: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    model: Mapped[Optional[str]] = mapped_column(String(128))
    token_usage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    trust: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    groundedness_score: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # answer | insight | search
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # -1 | 1
    comment: Mapped[Optional[str]] = mapped_column(Text)
    user_id: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (Index("ix_insights_status", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_doc_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="draft"
    )  # draft | review | approved | rejected
    author: Mapped[Optional[str]] = mapped_column(String(128))
    reviewer: Mapped[Optional[str]] = mapped_column(String(128))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    promoted_document_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Bundle(Base):
    """War-room incident pack definition."""

    __tablename__ = "bundles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # pack:linux-hang
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
