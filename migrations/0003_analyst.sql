-- PPM Analyst: runs, tasks, artifacts, benchmarks and teaching events.

CREATE TABLE IF NOT EXISTS analysis_run (
    run_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_label   text NOT NULL,
    question     text NOT NULL,
    plan_json    jsonb NOT NULL,
    status       text NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'running', 'awaiting_approval', 'completed', 'failed', 'rejected')),
    model        text,
    assumptions  jsonb NOT NULL DEFAULT '[]',
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz,
    total_ms     integer
);

CREATE INDEX IF NOT EXISTS analysis_run_status_idx ON analysis_run (status, created_at DESC);

CREATE TABLE IF NOT EXISTS analysis_task (
    task_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id         uuid NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    task_key       text NOT NULL,
    name           text NOT NULL,
    kind           text NOT NULL,
    depends_on     text[] NOT NULL DEFAULT '{}',
    code           text,
    code_hash      text,
    cache_key      text,
    status         text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'cached', 'validated', 'failed')),
    output_schema  jsonb NOT NULL DEFAULT '{}',
    validation     jsonb NOT NULL DEFAULT '{}',
    output_path    text,
    output_hash    text,
    error          text,
    started_at     timestamptz,
    finished_at    timestamptz,
    duration_ms    integer,
    UNIQUE (run_id, task_key)
);

CREATE INDEX IF NOT EXISTS analysis_task_run_idx ON analysis_task (run_id);

CREATE TABLE IF NOT EXISTS analysis_artifact (
    artifact_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       uuid NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    task_id      uuid REFERENCES analysis_task(task_id) ON DELETE SET NULL,
    kind         text NOT NULL CHECK (kind IN ('dataframe', 'chart', 'report', 'table')),
    name         text NOT NULL,
    path         text,
    mime         text,
    rows         integer,
    columns      jsonb NOT NULL DEFAULT '[]',
    summary      jsonb NOT NULL DEFAULT '{}',
    table_name   text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS analysis_artifact_run_idx ON analysis_artifact (run_id);

CREATE TABLE IF NOT EXISTS benchmark_case (
    case_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    question        text NOT NULL,
    expected_checks jsonb NOT NULL DEFAULT '{}',
    source_run_id   uuid REFERENCES analysis_run(run_id) ON DELETE SET NULL,
    status          text NOT NULL DEFAULT 'active',
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS teaching_event (
    event_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            uuid REFERENCES analysis_run(run_id) ON DELETE SET NULL,
    user_note         text NOT NULL,
    proposed_lesson   text,
    benchmark_case_id uuid REFERENCES benchmark_case(case_id) ON DELETE SET NULL,
    status            text NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected', 'applied')),
    created_at        timestamptz NOT NULL DEFAULT now()
);
