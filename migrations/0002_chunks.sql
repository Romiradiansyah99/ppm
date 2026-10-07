-- Phase 1: document chunks for cited retrieval (plan section 6, Phase 1).
-- Chunks are plain rows in Postgres (framework-neutral); every chunk keeps a
-- section_ref so answers can cite the exact sheet range or page.

CREATE TABLE IF NOT EXISTS document_chunk (
    chunk_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     uuid NOT NULL REFERENCES document(document_id) ON DELETE CASCADE,
    chunk_index     integer NOT NULL,
    section_ref     text NOT NULL,                 -- "Cost Plan!R13-R32" or "page 3 (part 2)"
    text_content    text NOT NULL,
    token_estimate  integer,
    embedding       vector(768),
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS document_chunk_document_idx ON document_chunk (document_id);
CREATE INDEX IF NOT EXISTS document_chunk_embedding_idx
    ON document_chunk USING hnsw (embedding vector_cosine_ops);
