-- Phase 4: deliverables and report runs (plan section 6, Phase 4).
-- The report draft is a workflow artifact: it exists in report_run, and the
-- exported file is written only by the export node - which is reachable only
-- after a named approver's sign-off interrupt.

CREATE TABLE IF NOT EXISTS deliverable (
    deliverable_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     uuid NOT NULL REFERENCES project(project_id),
    type           text NOT NULL,
    due_date       date,
    status         text NOT NULL DEFAULT 'open',
    author_id      uuid REFERENCES person(person_id),
    reviewer_id    uuid REFERENCES person(person_id),
    document_ref   text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS deliverable_project_idx ON deliverable (project_id, due_date);

CREATE TABLE IF NOT EXISTS report_run (
    run_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id   text NOT NULL UNIQUE,
    project_id  uuid REFERENCES project(project_id),
    status      text NOT NULL
        CHECK (status IN ('draft', 'awaiting_signoff', 'approved', 'rejected', 'failed')),
    draft       text,
    draft_path  text,
    approver_id uuid REFERENCES person(person_id),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
