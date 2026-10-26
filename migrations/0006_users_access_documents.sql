-- Users, long-term memory, generated documents, and role-filtered views.
-- Junior users see only `baseline` projects; senior managers see everything.
-- The views read `ppm.access_level` (set per transaction by the tool layer).

CREATE TABLE IF NOT EXISTS app_user (
    user_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username       text NOT NULL UNIQUE,
    display_name   text NOT NULL,
    password_hash  text NOT NULL,
    role           text NOT NULL CHECK (role IN ('senior_manager', 'junior')),
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_memory (
    memory_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    kind           text NOT NULL DEFAULT 'fact',
    content        text NOT NULL,
    source_run_id  uuid REFERENCES analysis_run(run_id) ON DELETE SET NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS user_memory_user_idx ON user_memory (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS generated_document (
    generated_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    template_id    uuid REFERENCES template(template_id) ON DELETE SET NULL,
    project_id     uuid REFERENCES project(project_id) ON DELETE SET NULL,
    title          text NOT NULL,
    path           text NOT NULL,
    status         text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'rejected')),
    fields         jsonb NOT NULL DEFAULT '{}',
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS generated_document_user_idx ON generated_document (user_id, created_at DESC);

ALTER TABLE project ADD COLUMN IF NOT EXISTS data_class text NOT NULL DEFAULT 'baseline'
    CHECK (data_class IN ('baseline', 'restricted'));

ALTER TABLE analysis_run ADD COLUMN IF NOT EXISTS user_id uuid
    REFERENCES app_user(user_id) ON DELETE SET NULL;
ALTER TABLE analysis_run ADD COLUMN IF NOT EXISTS access_level text NOT NULL DEFAULT 'baseline';

UPDATE project SET data_class = 'restricted' WHERE name LIKE 'Benchmark B - %';
UPDATE project SET data_class = 'baseline' WHERE name LIKE 'Benchmark A - %';

-- Client-identifiable sample documents are senior-only.
UPDATE document SET confidentiality_class = 'client_identifiable'
WHERE source_path ILIKE '%Barclays%'
   OR source_path ILIKE '%Chevron%'
   OR source_path ILIKE '%Citi%'
   OR source_path ILIKE '%kaiyun%';

-- Role-filtered views. The tool layer sets ppm.access_level to 'all' or
-- 'baseline' for the transaction; an unset value defaults to baseline.
CREATE OR REPLACE VIEW analyst_project AS
SELECT * FROM project
WHERE COALESCE(current_setting('ppm.access_level', true), 'baseline') = 'all'
   OR data_class = 'baseline';

CREATE OR REPLACE VIEW analyst_cost_plan AS
SELECT cp.* FROM cost_plan cp
JOIN project p ON p.project_id = cp.project_id
WHERE COALESCE(current_setting('ppm.access_level', true), 'baseline') = 'all'
   OR p.data_class = 'baseline';

CREATE OR REPLACE VIEW analyst_cost_line AS
SELECT cl.* FROM cost_line cl
JOIN cost_plan cp ON cp.costplan_id = cl.costplan_id
JOIN project p ON p.project_id = cp.project_id
WHERE COALESCE(current_setting('ppm.access_level', true), 'baseline') = 'all'
   OR p.data_class = 'baseline';

CREATE OR REPLACE VIEW analyst_benchmark_rate AS
SELECT br.* FROM benchmark_rate br
JOIN project p ON p.project_id = br.project_id
WHERE COALESCE(current_setting('ppm.access_level', true), 'baseline') = 'all'
   OR p.data_class = 'baseline';

CREATE OR REPLACE VIEW analyst_risk AS
SELECT r.* FROM risk r
JOIN project p ON p.project_id = r.project_id
WHERE COALESCE(current_setting('ppm.access_level', true), 'baseline') = 'all'
   OR p.data_class = 'baseline';

CREATE OR REPLACE VIEW analyst_change_event AS
SELECT c.* FROM change_event c
JOIN project p ON p.project_id = c.project_id
WHERE COALESCE(current_setting('ppm.access_level', true), 'baseline') = 'all'
   OR p.data_class = 'baseline';

CREATE OR REPLACE VIEW analyst_deliverable AS
SELECT d.* FROM deliverable d
JOIN project p ON p.project_id = d.project_id
WHERE COALESCE(current_setting('ppm.access_level', true), 'baseline') = 'all'
   OR p.data_class = 'baseline';

CREATE OR REPLACE VIEW analyst_document AS
SELECT * FROM document
WHERE COALESCE(current_setting('ppm.access_level', true), 'baseline') = 'all'
   OR confidentiality_class = 'internal';

CREATE OR REPLACE VIEW analyst_template AS SELECT * FROM template;
