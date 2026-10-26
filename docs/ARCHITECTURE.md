# Architecture

## Shape

```text
corpus/manifest.yaml ──► staged ingest ──► Postgres
                           │                 ├─ document / alias
                           │                 ├─ document_chunk (+ vector)
                           │                 ├─ template
                           │                 └─ ingest_run / ingest_staging

Streamlit ──► per-user conversation/memory ──► private Hermes role instance
    │                                              │
    │                                              └─ MCP tools ──► Postgres
    ├─ template finder (SQL) ──► template + document
    └─ cited RAG ──► chunks ──► Ollama ──► citation check
```

## Boundary rules

1. No LangChain, LangGraph or Deep Agents import/dependency exists in PPM.
2. Data is framework-neutral: ordinary Postgres tables, a vector column on
   `document_chunk`, YAML registries.
3. One write path: only the deterministic ingest state machine writes entity
   rows; the chat can create DRAFT documents and memory for its own user.
4. Public identity, threads and memory are PPM Postgres data, not Hermes profile
   state.
5. Tests inject deterministic model doubles through `ppm.models.set_test_doubles`.

## Ingest state machine

`classify -> load -> redact -> [stage review] -> chunk -> persist`

- `classify` reads the resolved manifest entry.
- `load` hashes the file, rejects exact duplicates, registers the document and
  parses it deterministically.
- `redact` scans every section for client names and records hits.
- If a client name, non-indexable licence or PDF without a text layer is found,
  the state is stored in `ingest_staging` and `ingest_run.status` becomes
  `awaiting_review`. There is no LangGraph interrupt; a review survives restarts
  because it is a database row.
- `review approve` or `review reject` loads the staged parsed document, applies
  the decision, chunks/embeds/persists, and deletes the staging row.
- Embedding failure stores NULL embeddings and `indexed_text_only`; literal
  search still works.

## Ask flow

`retrieve -> grade -> [refuse] -> generate -> citation_check`

- The template catalog is a first-class source and is cited like a chunk.
- Retrieval is one SQL statement: filters first, pgvector cosine second.
- `grade` refuses when nothing useful is retrieved or the top similarity is
  below `PPM_MIN_SIMILARITY`.
- `citation_check` removes every sentence without a valid `[S#]`. Unsourced
  claims are never paraphrased into an answer.

## Public chat

`app/main.py` is one Streamlit page. It resolves a cookie session, loads the
selected `conversation`, and calls the private Hermes role instance.

```text
user_session -> app_user -> conversation(thread_id, hermes_session_id)
                               │
                               ├─ chat_message (user/assistant, events)
                               ├─ user_preference / user_memory
                               └─ analysis_run / generated_document
```

Each turn issues a signed context token (`user_id`, `conversation_id`,
`access_level`, expiry). Hermes passes it to every MCP tool; the MCP server
verifies the signature and ownership. The baseline MCP instance is restricted to
baseline rows; the admin instance can see all. Ingest/review/reindex/seed are
admin UI/CLI operations, not chat tools.

Hermes built-in memory is disabled. The system prompt is rebuilt each turn from
the user's preferences, relevant memory and conversation summary. Messages are
saved by PPM after streaming; the UI never depends on `st.session_state` alone
for the thread.

## Analyst pipeline

```text
question -> planner -> AnalysisPlan (datasets + DAG + schemas)
         -> codegen (parallel per task) -> static analysis
         -> sandbox subprocess -> deterministic validators
         -> artifacts (dataframe/chart/report) -> report
```

- The plan is stored in `analysis_run`; tasks, code hashes, cache keys,
  validation and artifacts are separate rows.
- Datasets are SELECT-only SQL over an allowlisted catalog, wrapped in a
  read-only transaction with a statement timeout.
- Every task runs under `exports/analyst/<run_id>/` with a minimal environment,
  import allowlist and timeout.
- Cache key = code hash + input hashes. Validators run for every output.
- The Teach loop writes a `teaching_event`; it never edits prompts/code.

## Why the rate tables are seed-only today

The structured tables exist, but are seeded from anonymised benchmark samples.
Real office cost plans plug in through `ppm data seed`/the same extractors after
definitions are signed.
