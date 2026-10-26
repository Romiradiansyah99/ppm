-- Public multi-user accounts, persistent threads, preferences and memory.
-- Hermes sessions are working memory only; these tables are the source of truth.

ALTER TABLE app_user ADD COLUMN IF NOT EXISTS failed_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS locked_until timestamptz;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS last_login_at timestamptz;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS disabled_at timestamptz;

CREATE TABLE IF NOT EXISTS invite (
    invite_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email        text,
    role         text NOT NULL CHECK (role IN ('senior_manager', 'junior')),
    token_hash   text NOT NULL UNIQUE,
    expires_at   timestamptz NOT NULL,
    accepted_at  timestamptz,
    created_by   uuid REFERENCES app_user(user_id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS invite_pending_idx
    ON invite (expires_at) WHERE accepted_at IS NULL;

CREATE TABLE IF NOT EXISTS user_session (
    session_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    token_hash    text NOT NULL UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz,
    user_agent    text,
    ip_hash       text
);

CREATE INDEX IF NOT EXISTS user_session_user_idx
    ON user_session (user_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS login_attempt (
    attempt_id   bigserial PRIMARY KEY,
    username     text NOT NULL,
    ip_hash      text,
    success      boolean NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS login_attempt_username_idx
    ON login_attempt (lower(username), created_at DESC);

CREATE TABLE IF NOT EXISTS conversation (
    conversation_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id         text NOT NULL UNIQUE,
    user_id           uuid NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    hermes_session_id text UNIQUE,
    title             text NOT NULL DEFAULT 'New conversation',
    summary           text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    archived_at       timestamptz
);

CREATE INDEX IF NOT EXISTS conversation_user_idx
    ON conversation (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_message (
    message_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
    role            text NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content         text NOT NULL,
    events          jsonb NOT NULL DEFAULT '[]',
    usage           jsonb NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_message_conversation_idx
    ON chat_message (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS user_preference (
    user_id     uuid NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    pref_key    text NOT NULL,
    pref_value  text NOT NULL,
    source      text NOT NULL DEFAULT 'explicit',
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, pref_key)
);

ALTER TABLE user_memory ADD COLUMN IF NOT EXISTS conversation_id uuid
    REFERENCES conversation(conversation_id) ON DELETE SET NULL;
ALTER TABLE user_memory ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'user';
ALTER TABLE user_memory ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS user_memory_conversation_idx
    ON user_memory (conversation_id, created_at DESC);

ALTER TABLE analysis_run ADD COLUMN IF NOT EXISTS conversation_id uuid
    REFERENCES conversation(conversation_id) ON DELETE SET NULL;
ALTER TABLE generated_document ADD COLUMN IF NOT EXISTS conversation_id uuid
    REFERENCES conversation(conversation_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS analysis_run_conversation_idx
    ON analysis_run (conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS generated_document_conversation_idx
    ON generated_document (conversation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS audit_event (
    event_id     bigserial PRIMARY KEY,
    user_id      uuid REFERENCES app_user(user_id) ON DELETE SET NULL,
    action       text NOT NULL,
    target       text,
    detail       jsonb NOT NULL DEFAULT '{}',
    ip_hash      text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_event_user_idx
    ON audit_event (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS usage_event (
    usage_id       bigserial PRIMARY KEY,
    user_id        uuid REFERENCES app_user(user_id) ON DELETE SET NULL,
    conversation_id uuid REFERENCES conversation(conversation_id) ON DELETE SET NULL,
    kind           text NOT NULL,
    input_tokens   integer,
    output_tokens  integer,
    metadata       jsonb NOT NULL DEFAULT '{}',
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS usage_event_user_idx
    ON usage_event (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ingest_staging (
    thread_id   text PRIMARY KEY REFERENCES ingest_run(thread_id) ON DELETE CASCADE,
    entry       jsonb NOT NULL,
    parsed      jsonb,
    redacted    jsonb,
    stats       jsonb NOT NULL DEFAULT '{}',
    review      jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
