-- PPM Analyst: project data foundation.
-- Structured project entities. Every row carries provenance (document_id +
-- source_ref) so the analyst can cite the exact sheet cell.

CREATE TABLE IF NOT EXISTS project (
    project_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                   text NOT NULL UNIQUE,
    client_label           text,
    sector                 text,
    stage                  text,
    status                 text NOT NULL DEFAULT 'active',
    currency               text,
    start_date             date,
    end_date               date,
    gfa_m2                 numeric(14, 2),
    area_basis             text,
    source_document_id     uuid REFERENCES document(document_id) ON DELETE SET NULL,
    source_ref             text,
    confidentiality_class  text NOT NULL DEFAULT 'internal'
        CHECK (confidentiality_class IN ('internal', 'client_identifiable', 'client_confidential')),
    created_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS project_stage_idx ON project (stage);
CREATE INDEX IF NOT EXISTS project_sector_idx ON project (sector);

CREATE TABLE IF NOT EXISTS cost_plan (
    costplan_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
    version         integer NOT NULL DEFAULT 1,
    stage           text,
    plan_date       date,
    currency        text,
    total_cost      numeric(18, 2),
    gfa_m2          numeric(14, 2),
    area_basis      text,
    rate_basis      text,
    document_id     uuid REFERENCES document(document_id) ON DELETE SET NULL,
    source_ref      text,
    confidence      numeric(4, 3),
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, version)
);

CREATE INDEX IF NOT EXISTS cost_plan_project_idx ON cost_plan (project_id);
CREATE INDEX IF NOT EXISTS cost_plan_date_idx ON cost_plan (plan_date DESC);

CREATE TABLE IF NOT EXISTS cost_line (
    line_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    costplan_id   uuid NOT NULL REFERENCES cost_plan(costplan_id) ON DELETE CASCADE,
    wbs_code      text,
    element_code  text,
    description   text NOT NULL,
    qty           numeric(18, 4),
    unit          text,
    rate          numeric(18, 4),
    amount        numeric(18, 2),
    currency      text,
    spec_level    text,
    is_subtotal   boolean NOT NULL DEFAULT false,
    source_ref    text NOT NULL,
    confidence    numeric(4, 3),
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (costplan_id, source_ref)
);

CREATE INDEX IF NOT EXISTS cost_line_costplan_idx ON cost_line (costplan_id);
CREATE INDEX IF NOT EXISTS cost_line_element_idx ON cost_line (element_code);

CREATE TABLE IF NOT EXISTS benchmark_rate (
    rate_id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    costplan_id           uuid REFERENCES cost_plan(costplan_id) ON DELETE SET NULL,
    project_id            uuid REFERENCES project(project_id) ON DELETE SET NULL,
    element_code          text,
    description           text NOT NULL,
    rate                  numeric(18, 4) NOT NULL,
    unit                  text NOT NULL,
    date_normalised_to    date,
    location              text,
    sector                text,
    spec_level            text,
    client_identifiable   boolean NOT NULL DEFAULT false,
    source_ref            text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (costplan_id, description, source_ref)
);

CREATE INDEX IF NOT EXISTS benchmark_rate_element_idx ON benchmark_rate (element_code);
CREATE INDEX IF NOT EXISTS benchmark_rate_sector_idx ON benchmark_rate (sector, date_normalised_to DESC);

CREATE TABLE IF NOT EXISTS risk (
    risk_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         uuid NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
    category           text,
    description        text NOT NULL,
    likelihood         integer CHECK (likelihood BETWEEN 1 AND 5),
    impact             integer CHECK (impact BETWEEN 1 AND 5),
    score              integer GENERATED ALWAYS AS (likelihood * impact) STORED,
    cost_impact        numeric(18, 2),
    programme_impact   text,
    owner              text,
    status             text,
    review_date        date,
    mitigation         text,
    source_ref         text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, source_ref)
);

CREATE INDEX IF NOT EXISTS risk_project_idx ON risk (project_id);
CREATE INDEX IF NOT EXISTS risk_score_idx ON risk (score DESC);

CREATE TABLE IF NOT EXISTS change_event (
    change_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id             uuid NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
    change_ref             text,
    description            text NOT NULL,
    cause                  text,
    cost_impact            numeric(18, 2),
    cost_impact_agreed     numeric(18, 2),
    cost_impact_fees       numeric(18, 2),
    drawdown_from          text,
    time_impact_days       integer,
    status                 text,
    instruction_ref        text,
    raised_date            date,
    decided_date           date,
    source_ref             text NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, source_ref)
);

CREATE INDEX IF NOT EXISTS change_event_project_idx ON change_event (project_id);
CREATE INDEX IF NOT EXISTS change_event_status_idx ON change_event (status);

CREATE TABLE IF NOT EXISTS deliverable (
    deliverable_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
    type            text NOT NULL,
    due_date        date,
    status          text,
    author          text,
    reviewer        text,
    document_id     uuid REFERENCES document(document_id) ON DELETE SET NULL,
    source_ref      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, type, source_ref)
);

CREATE INDEX IF NOT EXISTS deliverable_project_idx ON deliverable (project_id);
CREATE INDEX IF NOT EXISTS deliverable_due_idx ON deliverable (due_date);
