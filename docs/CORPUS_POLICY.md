# CORPUS POLICY

## What may be indexed

| Class | Meaning | Indexed |
|---|---|---|
| `office_authored` | Templates and documents authored by the office | yes |
| `permitted` | Public or licensed content with explicit permission | yes |
| `third_party_reference` | Licensed third-party text (RICS, RIBA, FIDIC/JCT/NEC) | **no** - catalog and citation only |
| `restricted` | Unclear licence or client-restricted | review queue |
| `forbidden` | Must not enter the corpus | no |

Rules live in `corpus/manifest.yaml`, not in code. Every excluded file appears in
`ppm corpus report` with a reason.

## Client identity

- `corpus/client_blocklist.txt` is scanned against extracted text, not file names.
- Hits are replaced with `[CLIENT]` and recorded in `document.redaction_hits`.
- Template titles are redacted too (`ppm corpus redact`); only the local file
  path keeps the original name, because that is how the file is opened.
- Any hit forces a human review before embedding. The reviewer chooses
  `approve_redacted`, `reference_only` or `forbid`.
- Best-practice samples (Barclays PEP, Chevron/Julius Bar cost model, MBS
  dashboard) may be indexed only after redaction, and they never feed a rate
  table in v2.

## Retention

- Raw files stay in their original folders; PPM stores paths, hashes, extracted
  text and embeddings only.
- Deleting a document from PPM: delete from `document` (chunks cascade) and, if
  the source file is client material, delete the file per the client's retention
  schedule.
- `ppm reindex` rebuilds chunks from the source file after a blocklist or parser
  change; it does not re-ingest new files.

## Signed off by

- Owner: ____________________  Date: __________
- Scope: template and methodology corpus, office-internal only.
