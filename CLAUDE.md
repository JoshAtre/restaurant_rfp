# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend (from `backend/`)
```bash
source venv/bin/activate
pip install -r requirements.txt
python -c "from app.core.database import init_db; init_db()"   # create SQLite schema
uvicorn main:app --reload --port 8000
```
Config via `backend/.env` (see `app/core/config.py` for keys: `openai_api_key`, `usda_api_key`, `google_places_api_key`, SMTP vars, `restaurant_city/state/zip`, `llm_model`, `database_url`). SQLite file lives at `backend/data/rfp.db`.

There is no backend test suite or linter configured yet.

### Frontend (from `frontend/`)
```bash
npm install
npm run dev       # Vite dev server on :5173
npm run build     # production build
npm run lint      # eslint .
```

## Architecture

This is a 5-step RFP automation pipeline: **menu → recipes/ingredients → USDA pricing → local distributors → RFP emails → (quote monitoring)**. The backend is a FastAPI app; the frontend is React 19 + Vite + Recharts (no Tailwind despite README).

### Pipeline orchestration
`backend/app/pipeline/orchestrator.py` is the single entry point that runs steps 1–5 sequentially against a `PipelineRun` row, updating `current_step` and `step_N_status` columns on each transition so the frontend can poll `GET /api/pipeline/{run_id}/status`. Each step delegates to one service module:

1. `services/menu_parser.py` — LLM-based parse of `menus.raw_text` into `recipes`, `ingredients`, `recipe_ingredients` (dedupes ingredients across recipes by `ingredients.name UNIQUE`).
2. `services/usda_pricing.py` — Looks up each `Ingredient` in USDA FoodData Central and writes `IngredientPrice` snapshots. Note: do **not** pass `dataType` to the USDA API (see commit b3b7f98 — it caused 400s).
3. `services/distributor_finder.py` — Finds local distributors via Google Places, populates `distributors` and `distributor_ingredients`.
4. `services/email_sender.py` — Composes RFP drafts (`rfp_emails`, `status='draft'`); only actually sends SMTP if `send_emails=True` was passed to `/api/pipeline/run`.
5. `services/quote_monitor.py` — Inbound quote parsing (stub / nice-to-have).

The pipeline is kicked off by `POST /api/pipeline/run` in `main.py`, which currently `await`s `run_pipeline` directly rather than using the injected `BackgroundTasks` — the HTTP call blocks until the whole pipeline finishes.

### Data model
Models live in `app/models/tables.py`; the canonical schema (with column semantics) is documented in `README.md`. Key invariants:
- `ingredients.name` is UNIQUE — the parser must dedupe before insert.
- `recipe_ingredients` is the recipe↔ingredient junction with per-recipe `quantity`/`unit`/`notes`, UNIQUE on `(recipe_id, ingredient_id)`.
- `ingredient_prices` is append-only snapshots; "latest price" is computed by `ORDER BY fetched_at DESC LIMIT 1` (see `/api/ingredients` in `main.py`).
- `pipeline_runs` has per-step status columns rather than a separate events table — UI progress is read directly from these.

### API surface
All routes currently live in `backend/main.py` as a flat set of handlers (the `app/api/*` split described in the README is not yet wired up). The pipeline orchestrator and services are the only code that writes to most tables; the HTTP handlers are mostly read-only plus `POST /api/menus`, `POST /api/pipeline/run`, and `POST /api/emails/{id}/send`.

### Logging
Structured key=value logs (`run_id=... step=N status=...`) are emitted throughout the orchestrator and services — match this format when adding new log lines so pipeline runs stay greppable.

### Current status
Steps 1–4 work end-to-end (commit 681f8df). Step 5 (quote monitor) is scaffolding. Frontend components referenced in the README (`PipelineStepper`, `MenuInput`, etc.) may not all exist yet — check `frontend/src/components/` before assuming.
