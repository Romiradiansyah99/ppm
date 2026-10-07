# Data residency position - TEMPLATE

Status: **template - the strongest residency story available to this office;
state it plainly in the infosec review (plan section 7).**

## Statement

All PPM processing runs on office hardware in Indonesia:

- The database is local (PostgreSQL on the office machine / office server).
- All model inference and embedding calls execute on the same office hardware
  via Ollama/vLLM. The code enforces this: `PPM_RESIDENCY=local` makes the
  model layer refuse any API-backed provider (`src/ppm/models.py`).
- No document, chunk, embedding or answer travels to a cloud endpoint while
  the residency flag is `local`. There is no telemetry.

## What would change this

- A written residency position allowing an approved endpoint, after which
  `PPM_RESIDENCY=api` plus an approved `PPM_API_BASE` may be set - an explicit,
  logged, one-line change, not a default.

## Model list (fill in per deployment)

| Purpose | Model | Version | Hardware |
|---|---|---|---|
| Extraction | | | |
| Answers | | | |
| Embeddings | | | |

Signed: ____  Date: ____
