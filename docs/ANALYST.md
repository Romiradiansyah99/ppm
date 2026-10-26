# PPM ANALYST

PAT-style analyst for project data. The loop is fixed in code, not improvised by
an agent:

```
question -> plan (datasets + DAG + schemas) -> approve
         -> parallel codegen -> static analysis -> sandbox execute
         -> deterministic validation -> self-review/report -> artifacts
```

## What it is

- **Planner** turns a question into an `AnalysisPlan`: SQL datasets, dataframe
  tasks with declared output schemas, dependencies, and validation checks.
- **Codegen** writes one Python function per task. Demo/tests use deterministic
  code; production uses an OpenAI-compatible API.
- **Runtime** executes each task in its own subprocess, in a per-run directory,
  with a minimal environment (no `PPM_DSN`, no API key) and a hard timeout.
- **Validators** are ordinary Python and cannot be skipped: schema, row count,
  nulls, currency, units, rate arithmetic, and cost-plan reconciliation.
- **Artifacts** (dataframe, chart, report) are stored in Postgres and on disk.
  A later plan can use an artifact as a dataset.
- **Teach** stores a user correction and proposes a lesson; no automatic change
  to the harness.

## Chat surface

`app/main.py` is the only page: invite/login, then a single T&T-styled chat
column. The conversational harness is a private Hermes Agent service, not a
LangChain/LangGraph agent. Hermes calls PPM tools over MCP:

```
hermes-analyst (baseline) / hermes-admin (senior)
  └── MCP: schema_catalog, sql_query, document_search, plan_and_run,
           find_templates, create_document, remember_preference, corpus_stats
```

- `plan_and_run` remains a deterministic PPM tool; Hermes only chooses when to
  call it.
- Ingest/review/reindex/data operations are **not** chat tools. They live in the
  senior-only Streamlit Admin view and CLI, so a prompt injection cannot trigger
  a write.
- Every tool call carries a signed context token binding it to the current user,
  conversation and access level.
- Simple questions are answered directly. A todo list is used only for genuinely
  multi-step work.
- The UI consumes Hermes SSE progress/token events and stores them in
  `chat_message.events`. It is a custom seamless layout with no avatars, robot
  icons or logo.
- The UI uses Inter and the T&T palette: `#1E4479`, `#33A6E3`, `#F2EEE7`,
  `#D2DAE4`.

Threads and preferences are Postgres rows: `conversation.thread_id`,
`chat_message`, `user_preference`, `user_memory`. Hermes built-in memory is
disabled. `memory_context()` loads the user's preferences, relevant memories and
recent facts into the system prompt. The user can view/delete their own memory in
the sidebar.

## Access scope

- `senior_manager` (seed `senior`): `all`.
- `junior` (seed `junior`): `baseline`; restricted projects and
  client-identifiable documents are filtered by Postgres views and the RAG
  query. The agent cannot bypass this with SQL because only `analyst_*` views
  are allowed.

## Documents

`create_document` fills `.docx`/`.xlsx` templates, adds a draft banner, and
stores a `generated_document` row with status `draft`. The chat shows a download
button. Legacy `.doc`, client-identifiable template sources for junior users,
and restricted projects are refused.

## Data

`migrations/0002_project_data.sql` adds project, cost plan, cost line,
benchmark rate, risk, change event and deliverable tables. Every row keeps
`source_ref` (sheet and cell) and the source document where applicable.

`ppm data seed` parses the sample workbooks:

- Julius Bar and Chevron benchmark cost models: 10 anonymised projects, 37
  component lines and benchmark rates (cost/m2).
- Risk register template: 16 rated sample risks.
- Change control register: 1 sample change with cost and time impact.

`ppm data report` shows coverage, currency mix, null fields and reconciliation
mismatches.

## Plan format (short)

```json
{
  "question": "...",
  "assumptions": ["..."],
  "datasets": [{
    "name": "cost_lines",
    "source": "sql",
    "sql": "SELECT ... FROM cost_line ...",
    "schema": {"project": "str", "amount": "float"},
    "inspection": ["row_count", "null_rate"]
  }],
  "tasks": [{
    "id": "by_component",
    "name": "Cost by component",
    "kind": "dataframe",
    "depends_on": [],
    "description": "...",
    "output_schema": {"project": "str", "line_total": "float"},
    "validation": [{"check": "sum_reconciles", "params": {"...": "..."}}]
  }]
}
```

Plans are validated before execution: SELECT-only SQL, known dependencies, no
cycles, at most 20 tasks, every task declares its output schema and every chart
depends on a data task.

## Execution safety

- Per-run directory under `exports/analyst/<run_id>/`.
- Inputs are parquet files written by the tool layer.
- Generated code may import only pandas, numpy, matplotlib, json, math,
  statistics and `ppm_runtime`; `eval`, `exec`, `open`, `getattr` and dunder
  attributes are rejected.
- Subprocess environment is minimal; no database credentials or API key.
- Timeout and cache per task. Same code + inputs = cached artifact.

This is containment, not a hardened security sandbox. Run the service on an
office machine with least-privilege accounts.

## Validators

| Check | Use |
|---|---|
| `schema` | required output columns exist |
| `non_empty` | output has rows |
| `no_negative_amount` | amounts are non-negative |
| `currency_consistent` | one currency per group/plan |
| `unit_in_registry` | units are canonical |
| `rate_matches_amount` | `rate * qty ≈ amount` |
| `sum_reconciles` | component totals equal `cost_plan.total_cost` |
| `basis_declared` | cost/m2 analysis has GFA/NIA/IPMS |

## Commands

```powershell
.venv\Scripts\ppm.exe user seed
.venv\Scripts\ppm.exe user list
.venv\Scripts\ppm.exe memory list --user junior
.venv\Scripts\ppm.exe memory clear --user junior

.venv\Scripts\ppm.exe data seed
.venv\Scripts\ppm.exe data report

.venv\Scripts\ppm.exe analyst demo
.venv\Scripts\ppm.exe analyst plan "Komponen biaya terbesar?" --out plan.json
.venv\Scripts\ppm.exe analyst run --plan plan.json --auto --codegen api
.venv\Scripts\ppm.exe analyst ask "..." --auto --codegen api
.venv\Scripts\ppm.exe analyst status RUN_ID
.venv\Scripts\ppm.exe analyst eval --benchmarks data\analyst_benchmarks.yaml
.venv\Scripts\ppm.exe analyst teach RUN_ID --note "..."
.venv\Scripts\ppm.exe analyst benchmarks list
```

## API configuration

The planner and production codegen use an OpenAI-compatible endpoint:

```dotenv
PPM_RESIDENCY=api_approved
PPM_LLM_API_BASE=https://api.example.com/v1
PPM_LLM_API_KEY=...
PPM_LLM_MODEL=...
PPM_LLM_TEMPERATURE=0
PPM_ANALYST_EGRESS_ACK=Nama, 2026-10-19
```

`PPM_ANALYST_EGRESS_ACK` is required: it records the approval to send project
data (question, schema, sample rows) outside the office. Embeddings and the
existing document RAG stay local.

If these are not set, `analyst demo` still works (deterministic code) but
`analyst plan/ask/eval` intentionally fail.

## Limitations

- The seed data is sample/benchmark data, not live projects.
- Cost/m2 comparisons are only valid when `area_basis` is declared; the
  validator refuses otherwise.
- The planner is only as good as the model behind the API; benchmark it before
  trusting a new model.
- The sandbox is a subprocess, not a VM. Generated code is allowlisted and
  stripped of credentials, but the host is still shared.
- The Teach loop proposes lessons; it does not mutate prompts or code.
- The chat UI loads display history from `chat_message`; long-term memory is in
  `user_memory` / `user_preference`. Hermes has only a working session.
- The subprocess sandbox is containment, not a VM. Artifact files live under
  `exports/`; protect that folder at the OS level for restricted data.
- Memory stores preferences and facts the user stated. It is per-user and can be
  cleared, but treat it as sensitive office data and prune it on the retention
  schedule.
