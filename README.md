# Restaurant RFP Automation System

An end-to-end automated procurement pipeline that takes a restaurant menu as input, extracts recipes and ingredients, fetches USDA pricing data, finds local distributors, and sends RFP emails.

## Restaurant Menu Source

**Sweetgreen** — [https://www.sweetgreen.com/menu](https://www.sweetgreen.com/menu)

Chosen because Sweetgreen's ingredient-forward menu maps cleanly to USDA commodity data, and their farm-to-table model naturally fits the distributor sourcing workflow.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    React Frontend (Vite)                 │
│  Pipeline Visualizer │ Data Tables │ Email Preview       │
└──────────────────────┬──────────────────────────────────┘
                       │ REST API
┌──────────────────────▼──────────────────────────────────┐
│                  FastAPI Backend                         │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌────────┐ │
│  │  Menu    │→│ USDA     │→│Distributor│→│  Email │ │
│  │  Parser  │  │ Pricing  │  │  Finder   │  │ Sender │ │
│  └──────────┘  └──────────┘  └───────────┘  └────────┘ │
│       │              │             │             │       │
│  ┌────▼──────────────▼─────────────▼─────────────▼────┐ │
│  │                   SQLite Database                   │ │
│  │  menus │ recipes │ ingredients │ prices │ dist │ rfp│ │
│  └────────────────────────────────────────────────────┘ │
│                                                         │
│  External Services:                                     │
│  • OpenAI GPT-4o API (menu parsing, email compose)       │
│  • USDA FoodData Central API (pricing)                  │
│  • Google Places API (distributor search)               │
│  • SMTP / SendGrid (email delivery)                     │
└─────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer     | Technology               | Rationale                                    |
|-----------|--------------------------|----------------------------------------------|
| Backend   | Python 3.11 + FastAPI    | Async, fast, great for API orchestration      |
| Database  | SQLite (via SQLAlchemy)  | Zero config, portable, real SQL               |
| LLM       | OpenAI GPT-4o          | JSON mode, strong at recipe parsing   |
| Frontend  | React + Vite + Custom CSS | Newspaper-inspired design, no framework bloat |
| Email     | smtplib / SendGrid       | Built-in Python SMTP or free tier API         |
| Pricing   | USDA FoodData Central    | Free, official commodity pricing data         |

## Database Schema

### Core Tables

```sql
-- The menu being analyzed
CREATE TABLE menus (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    source_url  TEXT,
    raw_text    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Individual dishes from the menu
CREATE TABLE recipes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_id     INTEGER NOT NULL REFERENCES menus(id),
    name        TEXT NOT NULL,
    description TEXT,
    category    TEXT,  -- appetizer, entree, side, drink, etc.
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Canonical ingredient list (deduplicated across recipes)
CREATE TABLE ingredients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    usda_fdc_id     INTEGER,          -- matched USDA FoodData Central ID
    usda_search_term TEXT,            -- normalized name used for USDA lookup
    category        TEXT,             -- produce, protein, dairy, grain, etc.
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Junction: which ingredients are in which recipes, with quantities
CREATE TABLE recipe_ingredients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id       INTEGER NOT NULL REFERENCES recipes(id),
    ingredient_id   INTEGER NOT NULL REFERENCES ingredients(id),
    quantity        REAL,
    unit            TEXT,             -- lbs, oz, cups, each, etc.
    notes           TEXT,             -- "organic", "diced", etc.
    UNIQUE(recipe_id, ingredient_id)
);

-- USDA pricing data snapshots
CREATE TABLE ingredient_prices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ingredient_id   INTEGER NOT NULL REFERENCES ingredients(id),
    price           REAL,
    unit            TEXT,
    source          TEXT DEFAULT 'USDA',
    report_date     DATE,
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Local distributors
CREATE TABLE distributors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    address         TEXT,
    city            TEXT,
    state           TEXT,
    website         TEXT,
    source          TEXT,             -- 'google_places', 'manual', etc.
    place_id        TEXT,             -- Google Places ID
    rating          REAL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- What each distributor supplies
CREATE TABLE distributor_ingredients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    distributor_id  INTEGER NOT NULL REFERENCES distributors(id),
    ingredient_id   INTEGER NOT NULL REFERENCES ingredients(id),
    UNIQUE(distributor_id, ingredient_id)
);

-- Outbound RFP emails
CREATE TABLE rfp_emails (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    distributor_id  INTEGER NOT NULL REFERENCES distributors(id),
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    status          TEXT DEFAULT 'draft',  -- draft, sent, failed
    sent_at         TIMESTAMP,
    quote_deadline  DATE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Inbound quotes
CREATE TABLE quotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rfp_email_id    INTEGER REFERENCES rfp_emails(id),
    distributor_id  INTEGER NOT NULL REFERENCES distributors(id),
    ingredient_id   INTEGER NOT NULL REFERENCES ingredients(id),
    quoted_price    REAL,
    unit            TEXT,
    delivery_terms  TEXT,
    valid_until     DATE,
    raw_email_body  TEXT,
    parsed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Pipeline run log (for UI status tracking)
CREATE TABLE pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_id         INTEGER REFERENCES menus(id),
    status          TEXT DEFAULT 'pending',  -- pending, running, completed, failed
    current_step    INTEGER DEFAULT 0,
    step_1_status   TEXT DEFAULT 'pending',
    step_2_status   TEXT DEFAULT 'pending',
    step_3_status   TEXT DEFAULT 'pending',
    step_4_status   TEXT DEFAULT 'pending',
    step_5_status   TEXT DEFAULT 'pending',
    error_log       TEXT,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Project Structure

```
restaurant_rfp/
├── README.md
├── CLAUDE.md
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   ├── main.py                       # FastAPI app + all route handlers
│   ├── app/
│   │   ├── core/
│   │   │   ├── config.py             # Pydantic Settings, env vars
│   │   │   ├── database.py           # SQLAlchemy engine + session
│   │   │   ├── llm.py               # OpenAI client wrapper (call_llm)
│   │   │   └── units.py             # Unit normalization/conversion
│   │   ├── models/
│   │   │   └── tables.py            # SQLAlchemy ORM models (10 tables)
│   │   ├── schemas/
│   │   │   └── api.py               # Pydantic request/response models
│   │   ├── services/
│   │   │   ├── menu_parser.py       # Step 1: Menu → Recipes + Ingredients
│   │   │   ├── usda_pricing.py      # Step 2: USDA FoodData Central pricing
│   │   │   ├── distributor_finder.py # Step 3: Google Places / LLM fallback
│   │   │   ├── email_sender.py      # Step 4: Compose & send RFP emails
│   │   │   └── quote_monitor.py     # Step 5: Simulate quotes via LLM
│   │   └── pipeline/
│   │       └── orchestrator.py      # Runs steps 1-5 sequentially
│   └── data/
│       └── rfp.db                   # SQLite database file
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html                   # Loads Fraunces + JetBrains Mono fonts
│   └── src/
│       ├── main.jsx                 # React root mount
│       ├── App.jsx                  # All UI: stepper, form, 5 data panels
│       ├── App.css                  # Component styles, newspaper layout
│       └── index.css                # CSS variables, color palette, fonts
```

## Setup & Run

### Prerequisites
- Python 3.11+
- Node.js 18+
- API Keys: OpenAI, USDA FoodData Central, Google Places (optional), SendGrid (optional)

### Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Fill in API keys
python -c "from app.core.database import init_db; init_db()"
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev   # Runs on :5173
```

### Run the Pipeline
1. Open the UI at `http://localhost:5173`
2. Paste the Sweetgreen menu URL or text
3. Click "Run Pipeline" — watch each step execute and populate
4. Review parsed recipes, pricing trends, distributors, and email drafts
5. Approve and send RFP emails

## API Endpoints

| Method | Endpoint                    | Description                         |
|--------|-----------------------------|-------------------------------------|
| GET    | `/api/health`               | Health check                        |
| POST   | `/api/menus`                | Create a menu                       |
| GET    | `/api/menus/{id}/recipes`   | Get parsed recipes for a menu       |
| GET    | `/api/ingredients`          | List all ingredients with prices    |
| GET    | `/api/pricing/trends`       | Get USDA pricing trend data         |
| GET    | `/api/distributors`         | List found distributors             |
| GET    | `/api/emails`               | List RFP email drafts               |
| POST   | `/api/emails/{id}/send`     | Send an RFP email                   |
| POST   | `/api/quotes/simulate`      | Trigger quote simulation (Step 5)   |
| GET    | `/api/quotes/comparison`    | Get quote comparison table          |
| POST   | `/api/pipeline/run`         | Start a new pipeline run            |
| GET    | `/api/pipeline/{id}/status` | Get current pipeline status         |
