# DPIA / data-processing assessment - TEMPLATE

Status: **template - to be completed and signed by the office leader + infosec
liaison before any Phase 3 data (client-confidential ingestion) is enabled.**
Plan section 7 requires this document to exist *before* client data enters the
corpus.

## 1. What is processed

- Office-internal cost plans, lessons, templates (Tier A) - already live.
- (Phase 3, after this sign-off) client-supplied documents, only with contract
  permission or written consent, tagged with a confidentiality class and a
  project silo.

## 2. Where it is processed

- Postgres on office hardware. Models run on office hardware (Ollama/vLLM);
  no data leaves the machine while `PPM_RESIDENCY=local`. See
  data-residency-position.md.

## 3. Who can see what

- Retrieval runs as a dedicated read-only role under row-level security keyed
  to project membership, with a second guardrail check in every retrieval
  path (`src/ppm/guardrails.py`). The adversarial suite
  (`tests/test_silos.py`) demonstrates no cross-silo leakage.
- Admins (migrations, ingest) hold the write role; apps never write directly.

## 4. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Cross-client leakage | RLS + guardrail, adversarial tests in CI |
| Prompt injection through documents | Sources are data, not instructions; answers citation-checked |
| Retention overrun | retention-policy.md |

## 5. Sign-off

- Prepared by: ____  Date: ____
- Infosec review: ____  Date: ____
- Office leader: ____  Date: ____
