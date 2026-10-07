-- Phase 3: client silos (plan section 6, Phase 3). Silo enforcement is
-- defence in depth: Postgres row-level security for the retrieval app role,
-- plus a guardrail node in every retrieval path that re-checks membership.
--
-- Superusers bypass RLS, so retrieval must run as the dedicated ppm_app role;
-- the admin role keeps migrations, ingest and action graphs.

CREATE TABLE IF NOT EXISTS project_member (
    project_id      uuid NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
    person_id       uuid NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    role_on_project text,
    added_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, person_id)
);

-- read-only app role; change the password at deployment (runbook)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ppm_app') THEN
        CREATE ROLE ppm_app LOGIN PASSWORD 'ppm_app_dev_password';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO ppm_app;
GRANT SELECT ON document, cost_plan, benchmark_rate, document_chunk, project, project_member TO ppm_app;

-- person without password_hash, for display joins under the app role
CREATE OR REPLACE VIEW person_public AS
SELECT person_id, username, display_name, role, level, is_approver, created_at
FROM person;
GRANT SELECT ON person_public TO ppm_app;

-- --- row level security ---------------------------------------------------------

ALTER TABLE document ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS document_silo ON document;
CREATE POLICY document_silo ON document FOR SELECT TO ppm_app USING (
    confidentiality_class = 'internal'
    OR (project_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM project_member m
        WHERE m.project_id = document.project_id
          AND m.person_id = nullif(current_setting('ppm.person_id', true), '')::uuid
    ))
);

ALTER TABLE cost_plan ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cost_plan_silo ON cost_plan;
CREATE POLICY cost_plan_silo ON cost_plan FOR SELECT TO ppm_app USING (
    EXISTS (
        SELECT 1 FROM document d
        WHERE d.document_id = cost_plan.document_id
          AND (
              d.confidentiality_class = 'internal'
              OR (d.project_id IS NOT NULL AND EXISTS (
                  SELECT 1 FROM project_member m
                  WHERE m.project_id = d.project_id
                    AND m.person_id = nullif(current_setting('ppm.person_id', true), '')::uuid
              ))
          )
    )
);

ALTER TABLE benchmark_rate ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS benchmark_rate_silo ON benchmark_rate;
CREATE POLICY benchmark_rate_silo ON benchmark_rate FOR SELECT TO ppm_app USING (
    EXISTS (
        SELECT 1 FROM document d
        WHERE d.document_id = benchmark_rate.document_id
          AND (
              d.confidentiality_class = 'internal'
              OR (d.project_id IS NOT NULL AND EXISTS (
                  SELECT 1 FROM project_member m
                  WHERE m.project_id = d.project_id
                    AND m.person_id = nullif(current_setting('ppm.person_id', true), '')::uuid
              ))
          )
    )
);

ALTER TABLE document_chunk ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS document_chunk_silo ON document_chunk;
CREATE POLICY document_chunk_silo ON document_chunk FOR SELECT TO ppm_app USING (
    EXISTS (
        SELECT 1 FROM document d
        WHERE d.document_id = document_chunk.document_id
          AND (
              d.confidentiality_class = 'internal'
              OR (d.project_id IS NOT NULL AND EXISTS (
                  SELECT 1 FROM project_member m
                  WHERE m.project_id = d.project_id
                    AND m.person_id = nullif(current_setting('ppm.person_id', true), '')::uuid
              ))
          )
    )
);

ALTER TABLE project ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_silo ON project;
CREATE POLICY project_silo ON project FOR SELECT TO ppm_app USING (
    confidentiality_class = 'internal'
    OR EXISTS (
        SELECT 1 FROM project_member m
        WHERE m.project_id = project.project_id
          AND m.person_id = nullif(current_setting('ppm.person_id', true), '')::uuid
    )
);
