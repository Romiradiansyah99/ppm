# PPM - office truth layer and agent apps

Office-local source-of-truth platform ("PPM"), built to the PPM build plan v2
(LangChain/LangGraph edition). The full path is implemented, phase by phase:

| Phase | What it is | Status here |
|---|---|---|
| 0 | Truth layer + rate lookup (read-only) | built, verified |
| 1 | Cited RAG over the document index + eval harness | built, verified |
| 2 | Write path (compiled action graphs), lessons, trust weighting, device accounts | built, verified |
| 3 | Client silos: RLS + guardrail, adversarial suite, governance templates | built, verified |
| 4 | Deliverable tracker + report drafter with sign-off gate | built, verified |
| 5 | Change and risk registers with gated updates | built, verified |
| 6 | App-spec compiler + AI authoring + generic runner | built, verified |

**Read-only by default:** apps never write entity rows - every write is a
compiled action graph with validation, permission checks and, where configured,
a mandatory human approval interrupt. All model access goes through
`ppm.models` behind the residency flag (default: local Ollama only).

Phase 0's rate lookup is still the first app: office-internal cost plans are
ingested into Postgres with full provenance, rates are normalised against the
definition registry, and a read-only Streamlit page answers "what did this
cost?" with SQL filters + pgvector similarity.

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
Truth layer      ppm init-db | ingest FILE... | reindex [--force] | status | registry check
Review queue     ppm review list | show THREAD | approve THREAD [--drop 1,2] | reject THREAD
Search / ask     ppm search "text" [filters] | ask "question" [--literal] | eval rag
Actions          ppm action list | run NAME --user U --param k=v   (compiled write graphs)
Approvals        ppm approvals list | show RUN | approve RUN --user U | reject RUN
Knowledge        ppm user add/list/passwd | memory set/list --user U
Reporting        ppm report draft --project NAME --user U | show RUN | approve/reject RUN --user U
Silos            ppm silos check          (RLS + app-role report for infosec)
Apps (Phase 6)   ppm apps list | validate [file] | compile [file] | draft "..." --name X | approve FILE --user U
Diagnostics      ppm models check
```

## Layout

```
registry/entities/     definition registry: one YAML per entity, owner-signed
app_specs/             approved apps (views compile to SQL, actions to graphs)
app_specs_drafts/      AI-authored drafts waiting for human approval
migrations/            append-only SQL migrations (0001-0006)
src/ppm/               config, db (admin + silo app role), models (residency gate),
                       registry, loaders, normalise, guardrails, trust, auth,
                       compiler (views->SQL, drafts), eval,
                       workflows/ (ingest, rag, retrieval, actions, reporting)
app/                   Streamlit: rate lookup + Ask, Knowledge, Tracker,
                       Registers, Platform pages (all behind device login)
scripts/               dev_pg.ps1, pull_models.ps1, make_sample_data.py
governance/            DPIA / residency / retention templates for the infosec review
tests/                 105 tests incl. silo adversarial suite and graph E2E
docs/                  RUNBOOK.md, ARCHITECTURE.md
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

- 105/105 tests pass (`pytest`): registry, normalisation (Indonesian number and
  date formats), xlsx/pdf loaders, retrieval SQL, citation checking, compiled
  action graphs (gates, self-approval refusal, weight decay), reporting
  sign-off, the spec compiler, and full graph E2E against real
  Postgres 16.15 + pgvector 0.8.6
- silo adversarial suite: 100 direct + prompt-injection probes as one client's
  consultant return zero rows from another client's silo; RLS and the
  guardrail are each tested independently of the other
- every gate demonstrated end to end: ingest review interrupt, lesson
  promotion, peer-gated change updates, and report sign-off - all paused in
  one process and resumed in another (checkpoints survive restarts)
- report export exists only after an approver's sign-off; unsigned and
  rejected drafts never become files
- Phase 6 loop: `ppm apps draft "..."` -> validate -> `ppm apps approve` ->
  running app with compiled views and a gated action
- this machine intentionally has no models pulled; the deterministic `stub`
  provider ran every pipeline. Real usage starts with `scripts\pull_models.ps1`

## Not built (by design)

Writes outside action graphs, SSO, cross-border model calls, client-facing
output without a named approver, multi-tenancy, plugin marketplaces. The two
things code cannot grant remain human gates: infosec approval before
client-confidential ingestion (governance/ holds the templates) and partner
sign-off before any AI-drafted client deliverable leaves the office.

## Repo state

Git repository, committed phase by phase. The versioned artifacts the plan
refers to are `registry/` and `app_specs/`.

## License

MIT - see [LICENSE](LICENSE).
