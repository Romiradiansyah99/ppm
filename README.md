# PPM - Phase 0: truth layer + rate lookup

Office-local source-of-truth platform ("PPM"), built to the PPM build plan v2
(LangChain/LangGraph edition).

Phase 0 delivers the plan's first app: **rate/benchmark lookup** - office-internal
cost plans are ingested into Postgres with full provenance, rates are normalised
against the definition registry, and a read-only Streamlit page answers "what did
this cost?" with SQL filters + pgvector similarity.

**Read-only by design** (survives infosec review): no writes from the app, no
client-confidential data, no cloud calls. All model access goes through
`ppm.models` behind the residency flag (default: local Ollama only).

## Stack as built

| Piece | Choice |
|---|---|
| Database | PostgreSQL 16 + pgvector 0.8.6 (Docker image or native binaries) |
| Orchestration | LangGraph OSS, Postgres checkpointer, `interrupt()` review gate |
| LLM/embeddings | Ollama local models, configured in `.env`; `stub` provider for pipeline checks |
| Parsing | openpyxl (xlsx) and pdfplumber (pdf) custom loaders |
| Retrieval | SQL filters first, pgvector similarity second, behind one internal interface |
| UI | Streamlit |
| Registries | `registry/entities/*.yaml` (definitions), `app_specs/*.yaml` (apps) - git-versioned |

## Quickstart (this machine)

Postgres runs portably from `D:\ppm-env` (no installer, no service):

```powershell
scripts\dev_pg.ps1 init      # first time only: extract, initdb, create ppm + ppm_test
scripts\dev_pg.ps1 start     # after a reboot
scripts\dev_pg.ps1 status
```

Then, from the repo root:

```powershell
.venv\Scripts\python.exe -m ppm.cli init-db                    # or: .venv\Scripts\ppm.exe init-db
"The pipeline check needs no models:" 
$env:PPM_MODEL_PROVIDER="stub"; .venv\Scripts\python.exe -m ppm.cli ingest data\samples\*.xlsx
.venv\Scripts\streamlit run app\rate_lookup.py
```

`stub` is a deterministic dev-only provider: it proves the graph, review queue,
normalisation and search plumbing end-to-end on the synthetic samples. **Never
ingest real documents with it.**

## Real ingestion path (local models)

The residency story is: models run on office hardware. Pull once, scripted:

```powershell
scripts\pull_models.ps1                       # defaults: qwen3:8b + nomic-embed-text
.venv\Scripts\ppm.exe models check            # verifies both are present
.venv\Scripts\ppm.exe ingest "\\server\cost plans\*.xlsx"
```

Uncomment `PPM_MODEL_PROVIDER=ollama` in `.env` (it is the default). Model
names are one line each in `.env`; nothing else changes.

## Commands

```
ppm init-db                apply migrations + checkpointer tables
ppm ingest FILE...         run the ingest graph (review gate on low confidence)
ppm review list            documents paused for human review
ppm review show THREAD     what the reviewer is deciding on
ppm review approve THREAD [--drop 1,2] [--note ...]
ppm review reject THREAD [--note ...]
ppm search "text" [--sector X --spec-level Y --element-code Z --from D --to D --json]
ppm status                 counters, review queue, 90-day deprecation check, unresolved definitions
ppm registry check         definition registry report
ppm models check           Ollama/API endpoint diagnostics
```

## Layout

```
registry/entities/     definition registry: one YAML per entity, owner-signed
app_specs/             app specs (rate-lookup.yaml is the Phase 0 app)
migrations/            append-only SQL migrations
src/ppm/               config, db, models (residency gate), registry, loaders,
                       normalise, workflows/ (ingest graph, retrieval), cli
app/rate_lookup.py     Streamlit UI
scripts/dev_pg.ps1     portable dev Postgres; scripts/pull_models.ps1
tests/                 unit + database tests (pytest)
docs/                  RUNBOOK.md (bare machine -> running), ARCHITECTURE.md
```

## Phase 0 verification checklist (plan section 6)

- 100% of rates carry `document_id` + `source_ref` - enforced by schema + tests
- definition registry validates every ingested rate; unresolved definition fights
  are surfaced by `ppm status`, never silently guessed
- low-confidence or broken rows land in the human review queue (LangGraph
  `interrupt()` with a Postgres checkpointer - survives restarts)
- named-user acceptance test (5 real queries from memory, >=4 usable) is a
  founder/QS ritual, not code - see docs/RUNBOOK.md

## Verified on the build machine

- 52/52 tests pass (`pytest`): registry, normalisation (Indonesian number and
  date formats), xlsx/pdf loaders, SQL builder, app-spec validation, migrations,
  and the full ingest graph against real Postgres 16.15 + pgvector 0.8.6
- ingest -> review `interrupt()` -> new process (restart proof) -> `review show`
  -> `approve --drop` -> completed; every rate carries provenance; rows with
  missing units are flagged for a human instead of guessed
- search returns rates with project / cost-plan version / document hash /
  source ref; rates without sector or spec_level render flagged UNCLASSIFIED;
  without an embed model the search degrades to literal matching with a clear
  note (no silent wrong answers)
- Streamlit page boots and searches under `streamlit.testing.v1.AppTest`
- this machine intentionally has no models pulled; the deterministic `stub`
  provider ran the pipeline. Real ingestion starts with
  `scripts\pull_models.ps1`

## Repo state

Initialised as a git repository with **no commits yet** - the first snapshot is
yours to make (`git add -A; git commit -m "Phase 0 scaffold"`). The versioned
artifacts the plan refers to are `registry/` and `app_specs/`.

## Deliberately not in Phase 0

Writes from apps, client-confidential ingestion, SSO, generative answers,
multi-user accounts, cloud models, Phase 1+ apps. See the plan for sequencing
and the kill criteria that decide whether Phase 1 happens at all.

## License

MIT - see [LICENSE](LICENSE).
