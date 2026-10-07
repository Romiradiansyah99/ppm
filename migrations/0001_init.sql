-- PPM Phase 0 initial schema (plan section 6, step 2).
-- Entities: Document, Project, CostPlan, BenchmarkRate, plus operational
-- tables (ingest_run, search_log) and the migration ledger.
--
-- Registry-driven enum values (sector, stage, element codes, ...) are enforced
-- by the ingest workflow against registry/entities/*.yaml, not by CHECK
-- constraints here, so signed definition changes do not require migrations.
-- CHECKs below are only for values fixed by the plan itself.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migration (
    filename    text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project (
    project_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    global_project_no      text,                       -- Tier B reference, read-only
    name                   text NOT NULL UNIQUE,
    client_ref             text,                       -- Tier B reference; Client master is Phase 3+
    sector                 text,                       -- registry enum (GUESS until audited)
    services               text[],
    stage                  text,                       -- registry enum
    start_date             date,
    end_date               date,
    fee_idr                bigint,                     -- confidential
    status                 text NOT NULL DEFAULT 'active',
    confidentiality_class  text NOT NULL DEFAULT 'internal'
        CHECK (confidentiality_class IN ('internal', 'client_confidential', 'client_restricted')),
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document (
    document_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hash                   text NOT NULL UNIQUE,       -- sha256 of file bytes
    source_path            text NOT NULL,
    mime                   text NOT NULL,
    project_id             uuid REFERENCES project(project_id),
    author                 text,
    doc_date               date,
    confidentiality_class  text NOT NULL DEFAULT 'internal'
        CHECK (confidentiality_class IN ('internal', 'client_confidential', 'client_restricted')),
    ingest_status          text NOT NULL DEFAULT 'reference_only'
        CHECK (ingest_status IN ('indexed', 'reference_only', 'forbidden')),
    licence_class          text,                       -- plan section 13
    created_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cost_plan (
    costplan_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id             uuid NOT NULL REFERENCES project(project_id),
    version                integer NOT NULL DEFAULT 1,
    stage                  text NOT NULL,              -- order-of-magnitude|elemental|detailed|tender|post-tender
    plan_date              date NOT NULL,
    currency               text NOT NULL DEFAULT 'IDR',
    gfa_m2                 numeric(14, 2),
    area_basis             text,                       -- GFA|NIA|IPMS (definition fight 1)
    total_cost_idr         bigint,
    rate_basis             text,                       -- bare|incl_prelims|all_in (definition fight 4)
    elements               jsonb NOT NULL DEFAULT '[]',-- nested as per plan section 3.3
    basis_notes            text,
    author_id              text,
    approval_id            text,
    document_id            uuid NOT NULL REFERENCES document(document_id),
    document_ref           text NOT NULL,              -- "<sha256>:<source_path>" snapshot
    registry_version       text NOT NULL,              -- registry content hash at ingest time
    extraction_confidence  numeric(4, 3),
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, version)
);

CREATE TABLE IF NOT EXISTS benchmark_rate (
    rate_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    costplan_id          uuid NOT NULL REFERENCES cost_plan(costplan_id) ON DELETE CASCADE,
    document_id          uuid NOT NULL REFERENCES document(document_id),  -- provenance fast path
    element_code         text NOT NULL,
    description          text NOT NULL,
    rate_idr             bigint NOT NULL,
    unit                 text NOT NULL,                -- canonical unit from the registry
    date_normalised_to   date NOT NULL,
    location             text,
    sector               text,
    spec_level           text,                         -- low|medium|high; NULL renders "unclassified"
    client_identifiable  boolean NOT NULL DEFAULT false,
    source_ref           text NOT NULL,                -- e.g. "Rates!R42" or "page 3, table 2, row 7"
    embedding            vector(768),                  -- PPM_EMBED_DIM; NULL degrades to filters-only search
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (costplan_id, element_code, description, source_ref)
);

CREATE INDEX IF NOT EXISTS benchmark_rate_element_code_idx ON benchmark_rate (element_code);
CREATE INDEX IF NOT EXISTS benchmark_rate_sector_spec_idx ON benchmark_rate (sector, spec_level);
CREATE INDEX IF NOT EXISTS benchmark_rate_date_idx ON benchmark_rate (date_normalised_to DESC);
CREATE INDEX IF NOT EXISTS benchmark_rate_embedding_idx
    ON benchmark_rate USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS ingest_run (
    run_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id    text NOT NULL UNIQUE,                 -- langgraph checkpoint thread
    document_id  uuid REFERENCES document(document_id),
    source_path  text NOT NULL,
    status       text NOT NULL
        CHECK (status IN ('running', 'awaiting_review', 'completed', 'failed', 'duplicate', 'rejected')),
    stats        jsonb NOT NULL DEFAULT '{}',
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS search_log (
    search_id     bigserial PRIMARY KEY,
    user_label    text NOT NULL,                       -- private per-consultant history (app spec)
    query         text,
    filters       jsonb NOT NULL DEFAULT '{}',
    result_count  integer,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS search_log_user_time_idx ON search_log (user_label, created_at DESC);
