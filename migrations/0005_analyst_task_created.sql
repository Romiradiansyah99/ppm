-- Cache ordering for analyst tasks.

ALTER TABLE analysis_task ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
