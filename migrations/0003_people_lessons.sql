-- Phase 2: people, competencies, two-layer memory, and the write-path ledger
-- (plan section 6, Phase 2). Every gated write records an action_run row so a
-- paused approval survives restarts and shows up in the approver's queue.

CREATE TABLE IF NOT EXISTS person (
    person_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username      text NOT NULL UNIQUE,
    display_name  text NOT NULL,
    password_hash text NOT NULL,                     -- scrypt$salt$hash
    role          text NOT NULL DEFAULT 'consultant',
    level         text,
    is_approver   boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS competency_standard (
    competency_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pillar            text NOT NULL,
    name              text NOT NULL,
    level             text NOT NULL,
    description       text,
    evidence_required text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pillar, name, level)
);

CREATE TABLE IF NOT EXISTS person_competency (
    person_id     uuid NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    competency_id uuid NOT NULL REFERENCES competency_standard(competency_id) ON DELETE CASCADE,
    evidence      text,
    status        text NOT NULL DEFAULT 'claimed'
        CHECK (status IN ('claimed', 'endorsed', 'expired')),
    endorsed_by   uuid REFERENCES person(person_id),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (person_id, competency_id)
);

CREATE TABLE IF NOT EXISTS lesson_learned (
    lesson_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   uuid REFERENCES project(project_id),
    author_id    uuid NOT NULL REFERENCES person(person_id),
    text_content text NOT NULL,
    sector       text,
    stage        text,
    trust_weight numeric(5, 3) NOT NULL DEFAULT 0.5,
    layer        text NOT NULL DEFAULT 'private'
        CHECK (layer IN ('private', 'shared')),
    status       text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'pending_approval', 'shared', 'rejected', 'removed')),
    approver_id  uuid REFERENCES person(person_id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS lesson_learned_author_idx ON lesson_learned (author_id);
CREATE INDEX IF NOT EXISTS lesson_learned_status_idx ON lesson_learned (status);

-- every weight change is logged and explainable (plan section 6, Phase 2)
CREATE TABLE IF NOT EXISTS lesson_weight_log (
    log_id     bigserial PRIMARY KEY,
    lesson_id  uuid REFERENCES lesson_learned(lesson_id) ON DELETE CASCADE,
    old_weight numeric(5, 3),
    new_weight numeric(5, 3),
    reason     text NOT NULL,
    actor_id   uuid REFERENCES person(person_id),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- private layer: per-user facts (search habits, writing notes). The shared
-- layer is the promoted lesson store above.
CREATE TABLE IF NOT EXISTS private_memory (
    memory_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id  uuid NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    kind       text NOT NULL,
    content    jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (person_id, kind)
);

-- ledger for every compiled action graph run (approval queue reads this)
CREATE TABLE IF NOT EXISTS action_run (
    run_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id  text NOT NULL UNIQUE,
    app        text NOT NULL,
    action     text NOT NULL,
    actor_id   uuid REFERENCES person(person_id),
    status     text NOT NULL
        CHECK (status IN ('running', 'awaiting_approval', 'completed', 'rejected', 'failed')),
    params     jsonb NOT NULL DEFAULT '{}',
    result     jsonb NOT NULL DEFAULT '{}',
    error      text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
