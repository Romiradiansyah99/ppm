# Runbook

## Local rebuild

```powershell
cd "D:\01. Project\01. Active\07. PPM\01. Build\02. Build\ppm-v2"
python -m venv .venv-hermes
.venv-hermes\Scripts\python.exe -m pip install -r requirements.txt
.venv-hermes\Scripts\python.exe -m pip install -e .

scripts\dev_pg.ps1 start
Copy-Item .env.example .env
# fill DSN, model and (for a live chat) PPM_HERMES_*
.venv-hermes\Scripts\ppm.exe init-db
.venv-hermes\Scripts\ppm.exe user seed
.venv-hermes\Scripts\python.exe -m streamlit run app\main.py
```

Dev accounts are `senior`/`senior123` and `junior`/`junior123`. They are not
created when `PPM_PUBLIC_MODE=1`.

## Corpus bootstrap

```powershell
.venv-hermes\Scripts\ppm.exe corpus report
.venv-hermes\Scripts\ppm.exe corpus catalog
.venv-hermes\Scripts\ppm.exe corpus redact
.venv-hermes\Scripts\ppm.exe ingest --manifest corpus\manifest.yaml
```

`REVIEW` lines are waiting for a human:

```powershell
.venv-hermes\Scripts\ppm.exe review list
.venv-hermes\Scripts\ppm.exe review show <thread>
.venv-hermes\Scripts\ppm.exe review approve <thread> --licence office_authored
.venv-hermes\Scripts\ppm.exe review reject <thread>
```

Reviews are stored in `ingest_staging`; they survive a restart and are ordinary
database rows, not in-process interrupts.

## Structured data and analyst

```powershell
.venv-hermes\Scripts\ppm.exe data seed
.venv-hermes\Scripts\ppm.exe data report
.venv-hermes\Scripts\ppm.exe analyst demo
.venv-hermes\Scripts\ppm.exe analyst ask "..." --auto --codegen api
```

The planner/codegen path requires `PPM_RESIDENCY=api_approved`,
`PPM_ANALYST_EGRESS_ACK`, and `PPM_LLM_*`.

## Public accounts

```powershell
.venv-hermes\Scripts\ppm.exe user bootstrap-admin --username admin
.venv-hermes\Scripts\ppm.exe user invite --email person@example.com --role junior
.venv-hermes\Scripts\ppm.exe user invite --email senior@example.com --role senior_manager
.venv-hermes\Scripts\ppm.exe user list
.venv-hermes\Scripts\ppm.exe user revoke-sessions person
```

The recipient redeems the one-time code on the login page. There is no public
registration. Set `PPM_PUBLIC_MODE=1` in production so demo seed accounts are
never created.

## Hermes

See `docs/HERMES.md`. Quick checks:

```powershell
docker compose logs -f hermes-analyst
docker compose exec hermes-analyst curl -fsS http://127.0.0.1:8642/health
```

If the model is rejected, check `PPM_LLM_MODEL` (the current gateway expects
`deepseek/deepseek-v4.1-flash`) and the provider credentials.

## Tests

```powershell
.venv-hermes\Scripts\python.exe -m pytest -q
docker compose config
```

Tests marked `db` need `ppm2_test`; tests marked `model` need pulled Ollama
models.

## Reindex

```powershell
.venv-hermes\Scripts\ppm.exe reindex
```

This re-chunks indexed documents from their source files. New files are picked up
by `ppm ingest` or the senior Admin upload tab.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Database is not ready` | `scripts\dev_pg.ps1 start`; then `ppm init-db`. |
| `Hermes is not configured` | Set `PPM_HERMES_ANALYST_URL/KEY` and `PPM_HERMES_ADMIN_URL/KEY`. |
| `unsupported_model` | Use the model id the gateway expects; keep the provider prefix. |
| Chat says tool context error | `PPM_TOOL_SECRET` must match on web and both MCP containers. |
| Review stuck | `ppm review list`; approve/reject. The staged payload is in `ingest_staging`. |
| Junior cannot see restricted data | Expected: baseline views and baseline MCP instance. |
| `ppm ask` returns "No source found." | Check `ppm status`; outside-corpus questions are refused by design. |
| Draft download missing | Check that web and MCP share the `ppm_exports` volume and the row belongs to the user. |
| Cookie login loops | Set `PPM_COOKIE_SECURE=1` only behind HTTPS; clear the `ppm_session` cookie. |
| Public signup unavailable | Invite-only by design; use `ppm user invite`. |
