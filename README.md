# PPM — Hermes Agent office knowledge platform

PPM is a public, invite-only, multi-user project-cost and template assistant for
the office. **Hermes Agent is the only conversational harness.** PPM keeps the
deterministic data layer: Postgres + pgvector, corpus ingest, review gates,
cited retrieval, the analyst planner/executor, template drafts, and app-spec
validation.

There is no LangChain, LangGraph or Deep Agents dependency or import in this
repository. Hermes runs as a private Docker service; the browser never talks to
it directly.

## What is where

```text
Browser
  │ HTTPS
  ▼
Caddy ──► Streamlit web ──────────────► Postgres 16 + pgvector
              │ auth, threads, memory       users, sessions, conversations,
              │ artifact ownership         messages, preferences, audit
              │
              ├── private HTTP ──► hermes-analyst  (baseline)
              └── private HTTP ──► hermes-admin    (senior only)

hermes-analyst ── MCP ──► ppm-mcp-analyst  (baseline SQL scope)
hermes-admin   ── MCP ──► ppm-mcp-admin    (all data, admin tools)

Ollama (local)        RAG embeddings and corpus answers
DeepSeek / gateway    Hermes model
```

## How each person is remembered

Hermes Agent's built-in `MEMORY.md` / `USER.md` is **per Hermes profile**, not
per web user. It is deliberately disabled in this deployment. PPM owns identity
and memory:

| Data | Table | Scope |
|---|---|---|
| Account | `app_user` | one person |
| Login session | `user_session` | one browser session |
| Preferences | `user_preference` and `user_memory` | one person, all threads |
| Conversation/thread | `conversation.thread_id` | one person |
| Messages | `chat_message` | one thread |
| Hermes working session | `conversation.hermes_session_id` | one thread, backend only |
| Analysis artifacts | `analysis_run` + `analysis_artifact` | owner + thread |
| Drafts | `generated_document` | owner + thread |

On login PPM restores the user's most recent thread and loads their preferences
and memories into the system prompt. Every query and download checks the owner;
the browser cannot invent a thread ID or read another user's row. A short-lived
HMAC token binds each tool call to the current user, thread and access level.

After a turn PPM saves both messages. When the user asks it to remember
something, the agent can call `remember_preference`; PPM also extracts durable
preferences when the message signals one. Users can view and delete their own
memory in the sidebar.

## Quick start (local)

```powershell
python -m venv .venv-hermes
.venv-hermes\Scripts\python.exe -m pip install -r requirements.txt
.venv-hermes\Scripts\python.exe -m pip install -e .

scripts\dev_pg.ps1 start
Copy-Item .env.example .env
# fill DSN, PPM_HERMES_* if you have Hermes, and PPM_LLM_* for analyst/API work

.venv-hermes\Scripts\ppm.exe init-db
.venv-hermes\Scripts\ppm.exe user seed        # dev only; not for public mode
.venv-hermes\Scripts\python.exe -m streamlit run app\main.py
```

Dev login: `senior` / `senior123` or `junior` / `junior123`. They are not created
when `PPM_PUBLIC_MODE=1`.

The chat needs a private Hermes API service. Without one, the UI still signs in
and shows the history/memory surface, but sending a message reports that Hermes
is not configured.

## Docker / public deployment

```powershell
Copy-Item .env.example .env
# set PPM_SESSION_SECRET, PPM_TOOL_SECRET, PPM_HERMES_*_KEY,
# PPM_LLM_API_KEY, PPM_LLM_API_BASE, APP_DOMAIN, POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec web ppm user bootstrap-admin --username admin
docker compose exec web ppm user invite --email person@example.com --role junior
```

`deploy/hermes/` holds the two role configs and the shared `SOUL.md`. Hermes
ports are not published; only Caddy is public. See `docs/HERMES.md` and
`docs/PUBLIC_DEPLOY.md` for TLS, backups and the invite flow.

## Commands

```text
Agent         ppm agent "question" [--user senior] [--thread ID]
Accounts      ppm user bootstrap-admin | invite --email X --role junior
              ppm user list | passwd USER PASS | revoke-sessions USER
Truth layer   ppm init-db | status | models check
Corpus        ppm corpus report | catalog | redact
Data          ppm data seed | data report
Analyst       ppm analyst demo | plan Q | run --plan F --auto | ask Q --auto
Ingest        ppm ingest [PATH...] [--manifest F] [--limit N]
Review        ppm review list | show THREAD | approve THREAD | reject THREAD
Find / Ask    ppm find "text" | ppm ask "question"
Maintenance   ppm reindex
Validation    ppm apps validate [--strict]
```

The core write policy is unchanged: ingestion and review are deterministic and
human-gated; the public chat cannot ingest, approve, reindex or seed data.
Drafts are DRAFT until a person reviews them.

## Layout

```text
app/main.py             single-page Streamlit app (no avatars, T&T theme)
src/ppm/auth.py         accounts, invites, scrypt, login sessions, audit
src/ppm/conversations.py persistent threads and messages
src/ppm/tool_context.py signed per-turn tool context
src/ppm/hermes/         private Hermes HTTP/SSE client + prompts
src/ppm/mcp/            FastMCP server and plain-Python PPM tools
src/ppm/models.py       direct Ollama HTTP (no LangChain)
src/ppm/workflows/      linear RAG, chunking, staged ingest
src/ppm/analyst/        planner, codegen, sandbox, executor, documents
migrations/             0001-0007 (0007 = public chat/memory)
deploy/                 Hermes configs, Caddy, SOUL
docs/                   architecture, Hermes, public deployment, runbook
tests/                  unit + Postgres + Streamlit AppTest coverage
```

## Verification

```powershell
.venv-hermes\Scripts\python.exe -m pytest -q
docker compose config
```

The suite covers the no-framework import gate, scrypt/legacy auth, invite and
session lifecycle, per-user threads and memory, signed MCP context, Hermes SSE
parsing, staged ingest without LangGraph, RAG citations, the analyst executor,
and a Streamlit login/chat turn against a fake Hermes server.

## Security notes

- Hermes is never public; each role has its own private API key and data scope.
- Built-in Hermes memory and dangerous toolsets (terminal, file, browser, web,
  code execution, delegation, cron, messaging) are disabled.
- Public accounts are invite-only; no default production password.
- Cookies are random tokens stored hashed in Postgres; `Secure` is enabled in
  production.
- Downloaded artifacts and drafts are ownership-checked in the database.
- The deployment is prepared but not live; a VPS, DNS and TLS are operator steps.

MIT licence. See `docs/PUBLIC_DEPLOY.md` for the public runbook.
