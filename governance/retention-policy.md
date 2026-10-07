# Retention and deletion policy - TEMPLATE

Status: **template - complete before client documents are ingested.**

## Rules

1. **Office-internal documents** (Tier A): retained until the owner deletes
   them; `document.ingest_status` moves to `reference_only` to un-index
   without deleting provenance.
2. **Client-supplied documents** (Phase 3+): retained only as long as the
   contract permits; deleted on the retention schedule per client.
   Deletion = remove the file, `DELETE FROM document_chunk WHERE document_id =
   ...`, and mark the document row `forbidden` so re-ingest is blocked.
3. **Benchmark rates derived from client plans**: stored client-stripped by
   construction (`client_identifiable=false`); if a client withdraws consent,
   a takedown query removes rates by `document_id`.
4. **Search history** (`search_log`): per-consultant instrumentation, deleted
   after 12 months.
5. **Audit**: every deletion is a logged SQL statement in the runbook log;
   `pg_dump` exports inherit the same rules.

## Takedown procedure (one client, one afternoon)

```sql
DELETE FROM document_chunk WHERE document_id IN (SELECT document_id FROM document WHERE project_id = :silo);
DELETE FROM benchmark_rate WHERE document_id IN (...);
UPDATE document SET ingest_status = 'forbidden' WHERE ...;
```

Signed: ____  Date: ____
