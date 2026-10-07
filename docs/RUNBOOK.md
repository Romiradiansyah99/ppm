# RUNBOOK - rebuild from a bare machine

Target (plan section 15): a non-founder rebuilds PPM from a bare machine in
under one hour. Everything here is scripted; nothing is folklore.

## 0. Prerequisites

- Python 3.11+ (3.12+ preferred), git
- PostgreSQL 16 system: either Docker with the `pgvector/pgvector:pg16` image
  (`docker compose up -d`) or native PostgreSQL 16 + pgvector 0.8.6 binaries
- Ollama with the two models: `scripts\pull_models.ps1`
- The repo (zip or git clone). `.env` is NOT in git - copy `.env.example`.

## 1. Database

**Docker path (standard):**

```powershell
docker compose up -d          # pgvector/pgvector:pg16 on 127.0.0.1:5432
```

**Native path (the machine this was built on):**

```powershell
scripts\dev_pg.ps1 init       # downloads PG 16.15 binaries + pgvector 0.8.6,
                              # extracts to D:\ppm-env, initdb, starts, creates
                              # ppm and ppm_test with the vector extension
```

## 2. Python environment

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # pinned
.venv\Scripts\python.exe -m pip install -e . --no-deps
```

## 3. Configuration

```powershell
Copy-Item .env.example .env
# edit PPM_DSN; leave PPM_RESIDENCY=local and PPM_MODEL_PROVIDER=ollama
```

## 4. Models (scripted pull - not folklore)

```powershell
scripts\pull_models.ps1                    # qwen3:8b + nomic-embed-text by default
.venv\Scripts\ppm.exe models check
```

If the office box has no GPU, embeddings are light; the 8B chat model runs on
CPU for ingest (slower, fine for 50-100 documents in Phase 0).

## 5. Schema + smoke test

```powershell
.venv\Scripts\ppm.exe init-db
.venv\Scripts\ppm.exe ingest data\samples\synthetic_alpha_elemental.xlsx
.venv\Scripts\ppm.exe search "reinforcement bar"
.venv\Scripts\python.exe -m pytest          # full suite incl. database tests
```

Then bring the office online: `ppm user add` for the named people (first
approver with `--approver`), `ppm apps list` to see the compiled apps, and
`ppm silos check` before the infosec conversation.

## 6. The app

```powershell
.venv\Scripts\streamlit run app\rate_lookup.py
```

Hand the URL to the named Phase 0 user. Acceptance ritual (plan section 6):
they run five real queries from memory; four must land on a usable rate.

## 7. Quarterly rebuild drill

1. Stop Postgres. Delete `.venv` and `D:\ppm-env\pgdata` (keep `downloads/`).
2. Follow steps 1-5. Time it. If it exceeds one hour, fix the slow step.
3. A non-founder performs the drill at least once; record the date here:
   - drill 1: ____ (by ____)
   - drill 2: ____ (by ____)
4. Export drill: `pg_dump ppm > ppm-YYYYMMDD.sql` plus a copy of the repo;
   restore is `psql ppm < file`. One afternoon, per the plan's exit-cost rule.

## Credentials

- Dev database password lives in `.env` (dev only, loopback-only Postgres).
- Office deployment: credentials go into office-managed storage (password
  manager / secret store), never into the repo, never into chat.
- Model endpoints: Ollama on office hardware. `PPM_API_*` stays empty unless a
  written residency position allows an approved endpoint.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `database problem: connection refused` | `scripts\dev_pg.ps1 start` (or `docker compose up -d`) |
| `checkpoint` errors on ingest | `ppm init-db` |
| ingest says extraction failed, model not found | `scripts\pull_models.ps1`, then `ppm models check` |
| search returns "filters-only results" | embed model not pulled; filters still work, similarity is off |
| review queue stuck | `ppm review list` -> `show` -> `approve`/`reject`; checkpoints survive restarts |
