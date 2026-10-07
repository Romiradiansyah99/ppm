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
| section 6 Phase 1 cited RAG | `workflows/chunking.py` + `workflows/rag.py` (citation check), `eval.py`, `app/pages/1_Ask.py` |
| section 6 Phase 2 write path + memory | `workflows/actions.py` (spec actions -> graphs), `auth.py`, `trust.py`, `app/pages/2_Knowledge.py` |
| section 6 Phase 3 silos | `migrations/0004_silos.sql` (RLS + `ppm_app`), `guardrails.py`, `db.connection_app`, `tests/test_silos.py` |
| section 6 Phases 4-5 apps | `workflows/reporting.py` (sign-off gate), `app_specs/tracker.yaml`, `registers.yaml` |
| section 6 Phase 6 compiler | `compiler.py` (views -> SQL, draft validation/approval), `ppm apps`, `app/pages/5_Platform.py` |
| section 9 build-vs-buy | see "decisions" below |
| section 12 risk 3 (dirty data) | validation severities + review queue |

## The write path (phases 2, 4, 5, 6)

An app-spec action compiles to a LangGraph workflow:
`validate -> permission -> [approval interrupt] -> write -> finalise`.
Every run records an action_run row, so a paused approval survives restarts
and appears in one queue (`ppm approvals`, the Knowledge page). Columns are
whitelisted by the definition registry intersected with the live table; hooks
supply derived values (authors, trust weights, promotion semantics). The report
drafter is the same idea with a longer body: gather -> draft -> sign-off ->
export, where the export node is unreachable without the approver's interrupt.

AI app authoring (Phase 6) is gated twice: drafts land in `app_specs_drafts/`
and are validated against the registry + action compiler, then a human runs
`ppm apps approve` - nothing becomes a running app on the model's say-so.

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

- **The action compiler was built in Phase 2, not Phase 6.** Phases 2, 4 and 5
  all needed gated write workflows, so the YAML -> StateGraph machinery
  (`workflows/actions.py`) became the single write path immediately; Phase 6
  then added the view compiler and the draft/approve loop around it.
- **Silo enforcement is two independent layers.** RLS filters every query the
  app role runs, and the guardrail (`guardrails.py`) re-checks membership with
  an explicit admin-side query before rows reach a model or a table. Each is
  tested against the other's absence.
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

## Known gaps (intentional)

- PDF ingestion works but has only a synthetic smoke test; real scanned plans
  (images) are out of scope until OCR is justified.
- Per-consultant silo enforcement covers retrieval paths (RAG, rates, chunk
  search, compiled views); operational tables (tracker, registers) are
  office-internal by construction and un-siloed.
- Langfuse tracing is optional and unconfigured by default (no Docker here);
  `eval rag` writes local JSON reports instead.
- The review queue and approvals are CLI/UI driven; everything rides on
  `interrupt()`, so further surfaces can attach without touching the graphs.
