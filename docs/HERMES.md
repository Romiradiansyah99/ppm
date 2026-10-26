# Hermes Agent integration

## Why a private service

Hermes Agent is a source-checkout/container harness, not a pip dependency for
the app. It also has dangerous built-in tools (terminal, files, browser, web)
and a per-profile memory store. For a public app, those must not be shared:

- Hermes runs in Docker behind the private `backend` network.
- Streamlit is the only caller; the browser never sees a Hermes URL or key.
- There are two role instances: `hermes-analyst` (baseline) and
  `hermes-admin` (senior). Each has its own API key.
- Built-in memory and user profile are disabled.
- Only the PPM MCP tools plus `todo` remain enabled.
- Ingest, review, reindex, data seeding and app validation are not agent tools;
  they live in the senior-only Streamlit admin view and the CLI.

## Turn flow

1. The Streamlit session resolves to a PPM user.
2. PPM selects or creates a `conversation` row with a server-generated
   `thread_id` and stores a Hermes API session id on that row.
3. PPM builds a British-English system prompt containing the user's preferences,
   memory and conversation summary.
4. PPM issues a short-lived HMAC `context` token bound to:
   `user_id`, `conversation_id`, `access_level`, expiry.
5. Streamlit calls `POST /v1/chat/completions` on the private Hermes instance
   with `stream: true` and `X-Hermes-Session-Id`.
6. Hermes calls PPM tools over MCP. Every tool verifies the context token,
   checks conversation ownership and the role instance's access level, then
   runs the deterministic PPM function.
7. Streamlit consumes SSE (`content`, `reasoning_content`,
   `hermes.tool.progress`), saves messages and events, then reads
   `/api/sessions/{id}/messages` for full tool payloads (artifact ids, draft
   ids).
8. PPM writes both messages to `chat_message`; the next login restores the
   thread from Postgres.

Hermes session history is working memory. PPM Postgres is authoritative. If the
Hermes volume is lost, PPM creates a new session and can replay the recent
transcript; the user's thread and preferences remain.

## MCP surface

`src/ppm/mcp/server.py` exposes:

- `schema_catalog(context)`
- `sql_query(context, sql)` — SELECT/WITH only, `analyst_*` views, role scope
- `document_search(context, question)` — cited RAG
- `plan_and_run(context, question)` — deterministic planner/executor
- `find_templates(context, query)`
- `create_document(context, ...)` — DRAFT owned by the context user/thread
- `remember_preference(context, content, kind)`
- `corpus_stats(context)`
- `run_cost_demo(context, question)` — admin instance only

The server is started with `PPM_ACCESS_LEVEL=baseline` or `all`. A token minted
for a senior (`all`) is rejected by the baseline server and vice versa.

## Configuration

`deploy/hermes/analyst/config.yaml` and `admin/config.yaml` contain the role
config. `deploy/hermes/SOUL.md` is the shared persona.

Required environment on each Hermes container:

```text
API_SERVER_ENABLED=true
API_SERVER_HOST=0.0.0.0
API_SERVER_KEY=<role key>
PPM_LLM_API_BASE=<OpenAI-compatible base>
PPM_LLM_API_KEY=<key>
PPM_LLM_MODEL=deepseek/deepseek-v4.1-flash
HERMES_HOME=/opt/data
```

The Compose file passes these directly; no key is committed. The configs use
`${VAR}` placeholders for the model. If your Hermes release does not expand
placeholders in the model block, write the same values into
`deploy/hermes/<role>/.env` (ignored by Git) or replace them with your provider's
documented provider/model fields.

## Health and troubleshooting

```powershell
docker compose logs -f hermes-analyst
docker compose exec hermes-analyst curl -fsS http://127.0.0.1:8642/health
docker compose exec web ppm status
```

Common issues:

- `Hermes is not configured`: `PPM_HERMES_ANALYST_URL`/`KEY` is empty.
- `unsupported_model`: use the model id the gateway expects; the current default
  is `deepseek/deepseek-v4.1-flash` with the provider prefix.
- Tool calls rejected: the context token expired, the user's role changed, or
  the MCP instance has the wrong `PPM_ACCESS_LEVEL`.
- Hermes session missing: PPM recreates it on the next turn; the user-visible
  thread is unaffected.

## Upgrade policy

Pin `HERMES_IMAGE` to a tested version or digest. Hermes changes its API and
session semantics between releases; run the fake-SSE tests and a real smoke test
before moving the pinned version. Do not use `latest` in production.
