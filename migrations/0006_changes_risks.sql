-- Phase 5: change and risk registers (plan section 6, Phase 5).
-- Both are mastered in PPM now; all writes go through compiled action
-- graphs, with peer/owner gates on updates.

CREATE TABLE IF NOT EXISTS change_event (
    change_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       uuid NOT NULL REFERENCES project(project_id),
    description      text NOT NULL,
    cause            text,
    cost_impact_idr  bigint,
    time_impact_days integer,
    status           text NOT NULL DEFAULT 'identified'
        CHECK (status IN ('identified', 'priced', 'submitted', 'approved', 'rejected')),
    instruction_ref  text,
    identified_on    date,
    submitted_on     date,
    approved_on      date,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS change_event_project_idx ON change_event (project_id, status);

CREATE TABLE IF NOT EXISTS risk (
    risk_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES project(project_id),
    category    text NOT NULL DEFAULT 'risk'
        CHECK (category IN ('risk', 'assumption', 'issue', 'dependency')),
    description text NOT NULL,
    likelihood  integer CHECK (likelihood BETWEEN 1 AND 5),
    impact      integer CHECK (impact BETWEEN 1 AND 5),
    owner_id    uuid REFERENCES person(person_id),
    review_date date,
    status      text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'monitoring', 'closed', 'escalated')),
    mitigation  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS risk_project_idx ON risk (project_id, status);
