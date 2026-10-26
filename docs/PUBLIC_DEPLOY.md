# Public deployment runbook

This repository is deployable; no live VPS or DNS record was changed by the
rebuild. The intended host is a small VPS (2 vCPU / 4 GB RAM minimum, more for
local Ollama) running Docker Compose.

## 1. Prepare secrets

On the host, copy `.env.example` to `.env` and set at least:

```text
POSTGRES_PASSWORD=<long random>
PPM_PUBLIC_MODE=1
PPM_SESSION_SECRET=<32+ random bytes>
PPM_TOOL_SECRET=<32+ random bytes>
PPM_HERMES_ANALYST_KEY=<32+ random bytes>
PPM_HERMES_ADMIN_KEY=<32+ random bytes>
PPM_COOKIE_SECURE=1
APP_DOMAIN=ppm.example.com
PPM_LLM_API_BASE=<approved OpenAI-compatible endpoint>
PPM_LLM_API_KEY=<key>
PPM_LLM_MODEL=deepseek/deepseek-v4.1-flash
PPM_RESIDENCY=api_approved
PPM_ANALYST_EGRESS_ACK=<name + date>
```

Generate values with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Never commit `.env`, `deploy/hermes/*/.env`, exports or database volumes.

## 2. DNS and TLS

Point the domain's A/AAAA record at the VPS. Caddy obtains a certificate
automatically. Open only ports 80 and 443; the Compose file binds Streamlit to
localhost and Hermes/MCP/Postgres to the private Docker network.

## 3. Start

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f web
```

If you want local Ollama on the same host:

```bash
docker compose --profile models up -d ollama
docker compose exec ollama ollama pull qwen2.5:3b
docker compose exec ollama ollama pull nomic-embed-text
```

Alternatively point `PPM_OLLAMA_HOST` at an existing office Ollama host. If it is
unreachable, corpus answers degrade to literal search rather than inventing
sources.

## 4. Bootstrap the first admin

```bash
docker compose exec web ppm user bootstrap-admin --username admin
```

The command prompts for a password. Then create one-time invites:

```bash
docker compose exec web ppm user invite --email person@example.com --role junior
docker compose exec web ppm user invite --email senior@example.com --role senior_manager
```

Send the printed code through a private channel. The recipient uses **Redeem
invite** on the login page, chooses a username/password, and is signed in. There
is no public registration and no default production password.

## 5. Verify

- Sign in as the invited user; ask a light question and check that no todo list
  appears.
- Ask a multi-step cost question; the Activity panel should show tool events.
- Reload and sign in again; the last thread and preferences should be restored.
- Sign in as a second user; no threads, memory, artifacts or drafts from the
  first user should appear.
- As a senior, open **Admin**, upload a test document, approve/reject it in the
  review queue, and confirm the junior account cannot see that view.
- `docker compose exec web ppm apps validate --strict` (expect known draft
  warnings).

## 6. Backups

Back up both Postgres and the Hermes working data:

```bash
docker compose exec -T db pg_dump -U ppm ppm2 > ppm-$(date +%F).sql
docker run --rm -v ppm_ppm_exports:/data -v "$PWD":/backup alpine \
  tar czf /backup/exports-$(date +%F).tgz -C /data .
```

Keep `deploy/hermes/analyst` and `deploy/hermes/admin` with the deployment
backup. PPM Postgres is the source of truth for users, threads, preferences and
drafts; Hermes state is working memory and can be rebuilt.

Restore Postgres into an empty database, run `ppm init-db`, then restore the
files and start the stack. Test an invite/login and one chat turn before opening
the app.

## 7. Operations

- Upgrade: pull the new image/repo, run tests, `docker compose up -d --build`.
  Pin `HERMES_IMAGE`; do not use `latest`.
- Revoke a user's sessions: `docker compose exec web ppm user revoke-sessions USER`.
- Change a password: `docker compose exec web ppm user passwd USER NEWPASS`.
- Remove memory: the user can use the sidebar; operators can use
  `ppm memory clear --user USER`.
- Logs: `docker compose logs -f web hermes-analyst`. Never paste keys or
  transcripts into public tickets.
- Rate limiting: the app enforces a daily per-user message limit and login
  lockout. Add a reverse-proxy/WAF rate limit if the host is exposed to broad
  internet traffic.

## 8. Known limits

- Hermes Agent is source/container managed; there is no supported `pip install`
  for it. The image is pinned in Compose.
- Public registration is intentionally absent; invites are distributed by an
  operator.
- Local Ollama costs RAM/CPU. A separate office Ollama host is preferable.
- The actual domain, VPS, TLS certificate, email delivery and WAF are operator
  responsibilities; the repository only prepares the stack.
