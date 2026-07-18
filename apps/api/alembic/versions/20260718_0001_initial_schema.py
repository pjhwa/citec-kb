"""initial canonical schema with pgvector

Revision ID: 20260718_0001
Revises:
Create Date: 2026-07-18

PR-02: sources, documents, sections, chunks, embeddings, ingest_jobs,
checkitems, entities, lexicon, issue_frames, capacity/pricing,
queries/answers/feedback, insights, bundles.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("source_id", sa.String(length=64), sa.ForeignKey("sources.id", ondelete="SET NULL")),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=1024), server_default="", nullable=False),
        sa.Column("body_md", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("source_uri", sa.String(length=2048), nullable=True),
        sa.Column("lang", sa.String(length=16), server_default="ko", nullable=False),
        sa.Column("evidence_grade", sa.String(length=16), server_default="B", nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=True),
        sa.Column("domain", sa.String(length=64), nullable=True),
        sa.Column("work_type", sa.String(length=64), nullable=True),
        sa.Column("path_l2", sa.String(length=512), nullable=True),
        sa.Column("path_l3", sa.String(length=512), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("source_type", "external_id", name="uq_documents_source_external"),
    )
    op.create_index("ix_documents_source_type", "documents", ["source_type"])
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_updated_at", "documents", ["updated_at"])
    op.create_index(
        "ix_documents_metadata_gin",
        "documents",
        ["metadata"],
        postgresql_using="gin",
    )

    op.create_table(
        "document_sections",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=64),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("heading_path", sa.String(length=2048), server_default="", nullable=False),
        sa.Column("level", sa.Integer(), server_default="1", nullable=False),
        sa.Column("body_md", sa.Text(), server_default="", nullable=False),
        sa.Column("token_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ordinal", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_document_sections_document_id", "document_sections", ["document_id"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=64),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "section_id",
            sa.String(length=64),
            sa.ForeignKey("document_sections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ordinal", sa.Integer(), server_default="0", nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("header_context", sa.Text(), server_default="", nullable=False),
        sa.Column("token_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_section_id", "chunks", ["section_id"])
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index("ix_chunks_is_active", "chunks", ["is_active"])

    op.create_table(
        "embeddings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "chunk_id",
            sa.String(length=64),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=128), server_default="BAAI/bge-m3", nullable=False),
        sa.Column("dim", sa.Integer(), server_default=str(EMBEDDING_DIM), nullable=False),
        sa.Column("vector", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("chunk_id", "model", name="uq_embeddings_chunk_model"),
    )
    # HNSW for cosine similarity (pgvector)
    op.execute(
        f"""
        CREATE INDEX ix_embeddings_vector_hnsw
        ON embeddings
        USING hnsw (vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )

    op.create_table(
        "ingest_jobs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("source_id", sa.String(length=64), sa.ForeignKey("sources.id", ondelete="SET NULL")),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_ingest_jobs_source_started", "ingest_jobs", ["source_id", "started_at"])

    op.create_table(
        "checkitems",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("lang", sa.String(length=8), server_default="KO", nullable=False),
        sa.Column("area", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("category_1", sa.String(length=128), nullable=True),
        sa.Column("subcategory", sa.String(length=128), nullable=True),
        sa.Column("subject", sa.String(length=512), server_default="", nullable=False),
        sa.Column("check_method", sa.Text(), nullable=True),
        sa.Column("check_criteria", sa.Text(), nullable=True),
        sa.Column("check_result", sa.Text(), nullable=True),
        sa.Column("risk_if_vulnerable", sa.Text(), nullable=True),
        sa.Column("remediation", sa.Text(), nullable=True),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column(
            "document_id",
            sa.String(length=64),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("code", "lang", name="uq_checkitems_code_lang"),
    )
    op.create_index("ix_checkitems_area", "checkitems", ["area"])
    op.create_index("ix_checkitems_category", "checkitems", ["category_1"])
    op.create_index("ix_checkitems_tsv", "checkitems", ["tsv"], postgresql_using="gin")

    op.create_table(
        "entities",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("canonical_name", sa.String(length=256), nullable=False),
        sa.Column("type", sa.String(length=64), server_default="business_system", nullable=False),
        sa.Column("customer", sa.String(length=256), nullable=True),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "host_patterns",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "env_hints",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "document_entities",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.String(length=64),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.String(length=64),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), server_default="1.0", nullable=False),
        sa.UniqueConstraint("document_id", "entity_id", name="uq_document_entities"),
    )
    op.create_index("ix_document_entities_entity_id", "document_entities", ["entity_id"])

    op.create_table(
        "lexicon_terms",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("canonical", sa.String(length=256), nullable=False),
        sa.Column(
            "variants",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.UniqueConstraint("canonical", name="uq_lexicon_canonical"),
    )

    op.create_table(
        "issue_frames",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=64),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("symptom", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("workaround", sa.Text(), nullable=True),
        sa.Column(
            "components",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("environment", sa.String(length=32), nullable=True),
        sa.Column(
            "commands",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("quality", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "raw_extract",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_issue_frames_document_id", "issue_frames", ["document_id"])

    op.create_table(
        "capacity_rules",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("basis", sa.String(length=64), server_default="1안", nullable=False),
        sa.Column("period_days", sa.Integer(), server_default="7", nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("unit_kind", sa.String(length=32), server_default="host", nullable=False),
        sa.Column("mm_per_field_week", sa.Float(), server_default="0.25", nullable=False),
        sa.Column("source_ref", sa.String(length=512), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
    )
    op.create_index("ix_capacity_rules_field", "capacity_rules", ["field"])

    op.create_table(
        "pricing_rules",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("field_group", sa.String(length=64), nullable=False),
        sa.Column("unit_kind", sa.String(length=32), server_default="host", nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=8), server_default="KRW", nullable=False),
        sa.Column("source_ref", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
    )

    op.create_table(
        "queries",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column(
            "filters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(length=64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_queries_created_at", "queries", ["created_at"])

    op.create_table(
        "answers",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "query_id",
            sa.String(length=64),
            sa.ForeignKey("queries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("answer_md", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column(
            "token_usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "trust",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("groundedness_score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "feedback",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "insights",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("body_md", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "source_doc_ids",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("author", sa.String(length=128), nullable=True),
        sa.Column("reviewer", sa.String(length=128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "promoted_document_id",
            sa.String(length=64),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_insights_status", "insights", ["status"])

    op.create_table(
        "bundles",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Seed default fs_raw source
    op.execute(
        """
        INSERT INTO sources (id, type, name, config, status)
        VALUES (
          'fs_raw',
          'fs_raw',
          'Local raw corpus',
          '{"path": "/data/raw"}'::jsonb,
          'active'
        )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    for table in [
        "bundles",
        "insights",
        "feedback",
        "answers",
        "queries",
        "pricing_rules",
        "capacity_rules",
        "issue_frames",
        "lexicon_terms",
        "document_entities",
        "entities",
        "checkitems",
        "ingest_jobs",
        "embeddings",
        "chunks",
        "document_sections",
        "documents",
        "sources",
    ]:
        op.drop_table(table)
