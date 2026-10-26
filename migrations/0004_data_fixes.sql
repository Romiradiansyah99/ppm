-- Data quality fixes discovered while seeding the sample workbooks.

ALTER TABLE risk ADD COLUMN IF NOT EXISTS risk_ref text;
ALTER TABLE risk ADD COLUMN IF NOT EXISTS document_id uuid REFERENCES document(document_id) ON DELETE SET NULL;
ALTER TABLE change_event ADD COLUMN IF NOT EXISTS document_id uuid REFERENCES document(document_id) ON DELETE SET NULL;
