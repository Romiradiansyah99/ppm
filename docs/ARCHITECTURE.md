# ARCHITECTURE - how the build maps to the plan

## Plan section -> implementation

| Plan | Here |
|---|---|
| section 3 entity dictionary | `migrations/0001_init.sql`, `registry/entities/*.yaml` |
| section 3.4 definition registry | `registry/entities/*.yaml` + `src/ppm/registry.py` (content-hash version, unresolved-definition report) |
| section 4 app spec format | `app_specs/rate-lookup.yaml` + `src/ppm/appspec.py` (read-only subset; full compiler is Phase 6) |
| section 6 Phase 0 step 2 | migrations + `ppm init-db` |
| section 6 Phase 0 step 3 ingest graph | `src/ppm/workflows/ingest.py` (LangGraph StateGraph, Postgres checkpointer, review interrupt) |
| section 6 Phase 0 step 5 retrieval | `src/ppm/workflows/retrieval.py` + `app/rate_lookup.py` |
| section 9 build-vs-buy | see "decisions" below |
| section 12 risk 3 (dirty data) | validation severities + review queue |

## The boundary rules (framework churn mitigation, plan section 9)

1. **Only `ppm.models`, `ppm.workflows`, `ppm.loaders` import langchain/langgraph.**
   Everything else (config, db, registry, normalise, appspec, cli, UI) is plain
   Python + SQL. The swap surface if the framework is abandoned: three files.
2. **Framework-neutral data.** Entities live in ordinary Postgres tables; the
   embedding is a column on `benchmark_rate`, not a framework-owned table.
   Registries are YAML. Export is `pg_dump` + folder copy.
3. **One write path.** Entity rows are written only by the ingest graph.
   Apps never write (the Streamlit page is read-only; `search_log` is
   instrumentation, not an entity).
4. **Registry-driven validation.** Enum/unit/element-code rules live in YAML
   signed by named owners; the graph enforces them; DB CHECKs only cover
   values fixed by the plan itself.

## Ingest data flow

```
file -> load (openpyxl/pdfplumber)         plain rows + source refs ("Sheet!R12")
     -> hash + register Document            sha256, dedupe on exact re-ingest
     -> extract (structured output LLM;     CostPlanExtraction (Pydantic),
        stub provider for dev only)         confidence per element, client stripped
     -> validate against registry           errors drop rows; warns are listed;
                                            review_required if errors or low confidence
     -> interrupt() if review_required  <-> ppm review show/approve/reject
     -> normalise                           units -> canonical, IDR parsing,
                                            rate = amount/qty, date, redaction pass
     -> persist (single transaction)        Project upsert, CostPlan version++,
                                            BenchmarkRate rows + embeddings
```

## Search data flow

```
query -> embed (Ollama, residency-gated)
      -> one SQL statement: WHERE structured filters
         ORDER BY embedding <=> query            (missing embedding => filters only)
      -> rows carry provenance: document_id, hash, source_path, source_ref,
         costplan version, project name
      -> search_log row (private per user_label)
```

## Decisions made while building (and why)

- **Retrieval is one SQL statement, not a langchain-postgres vector store.**
  Plan section 9 says "pgvector via langchain-postgres", but section 9's own
  churn-mitigation rule says keep data framework-neutral. A PGVector store owns
  its schema; a column on the entity table does not. The LangChain touchpoint is
  reduced to a `BaseRetriever` adapter (`as_retriever`), swappable in a day.
- **`stub` model provider** exists so the pipeline can be exercised on synthetic
  data before any model is pulled (or after an endpoint breaks). It writes
  deterministic hash vectors, so the full embedding -> pgvector SQL path is
  verified end-to-end even without models. It is loudly labelled dev-only;
  `ppm models check` reports it, and similarity on it is meaningless by design.
- **Dev machine runs portable Postgres 16.15 + community pgvector 0.8.6 builds**
  under `D:\ppm-env` (no installer, no service). Office deployment uses the
  official `pgvector/pgvector:pg16` image or a native install; the code only
  sees `PPM_DSN`.
- **Embedding dimension is fixed at 768** in the migration (nomic-embed-text).
  Changing the embed model means a new migration; `ppm.models` checks the
  dimension at runtime and fails loudly instead of writing mixed vectors.
- **`ppm_test` database** exists so the test suite never touches office data.

## Known Phase 0 gaps (intentional)

- PDF ingestion works but has only a synthetic smoke test; real scanned plans
  (images) are out of scope until OCR is justified.
- `permission_rule` is carried in the app spec but not enforced yet - Phase 0
  data is office-internal and client-stripped by construction; silo enforcement
  (RLS + guardrail node) is Phase 3 per the plan.
- The review queue is CLI-driven; it rides on `interrupt()`, so a Streamlit
  review pane can attach later without touching the graph.
