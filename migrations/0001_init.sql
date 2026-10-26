-- PPM v2 initial schema: document corpus, chunks, template catalog.
-- Rate/cost-plan tables are intentionally absent until that phase starts.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document (
    document_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hash                   text NOT NULL UNIQUE,
    source_path            text NOT NULL,
    canonical_path         text NOT NULL,
    source_collection      text NOT NULL,
    mime                   text NOT NULL,
    doc_date               date,
    licence_class          text NOT NULL DEFAULT 'unknown'
        CHECK (licence_class IN ('office_authored', 'permitted', 'third_party_reference',
                                 'restricted', 'forbidden', 'unknown')),
    confidentiality_class  text NOT NULL DEFAULT 'internal'
        CHECK (confidentiality_class IN ('internal', 'client_identifiable', 'client_confidential')),
    ingest_status          text NOT NULL DEFAULT 'pending'
        CHECK (ingest_status IN ('pending', 'awaiting_review', 'indexed', 'indexed_text_only',
                                 'reference_only', 'forbidden', 'failed', 'duplicate')),
    redaction_hits         jsonb NOT NULL DEFAULT '[]',
    extractor              text,
    stats                  jsonb NOT NULL DEFAULT '{}',
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS document_status_idx ON document (ingest_status);
CREATE INDEX IF NOT EXISTS document_collection_idx ON document (source_collection);

CREATE TABLE IF NOT EXISTS document_alias (
    alias_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id        uuid NOT NULL REFERENCES document(document_id) ON DELETE CASCADE,
    source_path        text NOT NULL UNIQUE,
    source_collection  text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS document_alias_document_idx ON document_alias (document_id);

CREATE TABLE IF NOT EXISTS document_chunk (
    chunk_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     uuid NOT NULL REFERENCES document(document_id) ON DELETE CASCADE,
    chunk_index     integer NOT NULL,
    section_ref     text NOT NULL,
    text_content    text NOT NULL,
    token_estimate  integer,
    embedding       vector(768),
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS document_chunk_document_idx ON document_chunk (document_id);
CREATE INDEX IF NOT EXISTS document_chunk_embedding_idx
    ON document_chunk USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS template (
    template_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         uuid REFERENCES document(document_id) ON DELETE SET NULL,
    source_collection   text NOT NULL,
    code                text,
    title               text NOT NULL,
    stage               text NOT NULL DEFAULT 'unclassified',
    category            text,
    tags                text[] NOT NULL DEFAULT '{}',
    format              text NOT NULL,
    is_blank            boolean NOT NULL DEFAULT true,
    has_sample_content  boolean NOT NULL DEFAULT false,
    variant_of          uuid REFERENCES template(template_id) ON DELETE SET NULL,
    status              text NOT NULL DEFAULT 'active',
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- One canonical template per (collection, code). Variants are extra rows that
-- point at the canonical row through variant_of.
CREATE UNIQUE INDEX IF NOT EXISTS template_collection_code_idx
    ON template (source_collection, code)
    WHERE code IS NOT NULL AND variant_of IS NULL;

CREATE INDEX IF NOT EXISTS template_stage_idx ON template (stage);
CREATE INDEX IF NOT EXISTS template_category_idx ON template (category);
CREATE INDEX IF NOT EXISTS template_document_idx ON template (document_id);

CREATE TABLE IF NOT EXISTS ingest_run (
    run_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id    text NOT NULL UNIQUE,
    source_path  text NOT NULL,
    document_id  uuid REFERENCES document(document_id) ON DELETE SET NULL,
    status       text NOT NULL
        CHECK (status IN ('running', 'awaiting_review', 'completed', 'failed', 'duplicate', 'rejected')),
    stats        jsonb NOT NULL DEFAULT '{}',
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingest_run_status_idx ON ingest_run (status);

CREATE TABLE IF NOT EXISTS search_log (
    search_id     bigserial PRIMARY KEY,
    user_label    text NOT NULL,
    kind          text NOT NULL,
    query         text,
    filters       jsonb NOT NULL DEFAULT '{}',
    result_count  integer,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS search_log_user_time_idx ON search_log (user_label, created_at DESC);
