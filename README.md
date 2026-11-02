# Windward

UK Day-Ahead Wind Procurement dashboard. It takes the raw Elexon `WINDFOR` revision stream, applies a bias-corrected model, and gives a trader a clean 24-hour wind outlook with `P10`/`P50`/`P90` bands and a recommended tranche plan across the eight Day-1 decision cutoffs.

**Live app:** https://wisjy.vercel.app/

---

## Project layout

```text
.
├── README.md
├── render.yaml                  # Render Blueprint: Docker API + PostgreSQL 16
│
├── Frontend/                    # React dashboard, deployed on Vercel
│   ├── .figma/                  # Figma Make build/preview config
│   ├── public/
│   │   ├── assets/              # static SVG artwork
│   │   └── favicon.svg
│   ├── src/
│   │   ├── App.tsx              # dashboard: fetch, controls, KPIs, SVG chart
│   │   ├── index.css            # Tailwind import, fonts, component styles
│   │   ├── main.tsx             # React entry point
│   │   └── vite-env.d.ts
│   ├── dist/                    # local production build (gitignored)
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── pnpm-lock.yaml
│   ├── tsconfig.json
│   ├── vercel.json              # build settings + SPA rewrite
│   └── vite.config.ts
│
└── Windward_API/                # FastAPI forecasting backend, deployed on Render
    ├── backend/
    │   ├── __init__.py
    │   ├── main.py              # FastAPI app, CORS, timing middleware, CLI dispatcher
    │   ├── config.py            # env vars, cutoffs, feature columns, model path
    │   ├── schemas.py           # Pydantic v2 request/response schemas
    │   ├── cli_predict.py       # terminal procurement reports (single / all cutoffs)
    │   ├── db/
    │   │   ├── __init__.py
    │   │   ├── schema.sql        # PostgreSQL 16 DDL + causal indexes
    │   │   ├── models.py         # SQLAlchemy table definitions
    │   │   ├── database.py       # PostgreSQL connection + SQLite fallback
    │   │   ├── seed_db.py        # idempotent WINDFOR/FUELHH CSV seeder
    │   │   └── build_production_bundle.py
    │   ├── routers/
    │   │   ├── __init__.py
    │   │   ├── health.py         # GET /api/v1/system/health
    │   │   ├── cutoffs.py        # GET /api/v1/cutoffs/policy
    │   │   ├── forecast.py       # GET /api/v1/forecast/day-ahead
    │   │   ├── benchmark.py      # GET /api/v1/benchmark/january-overview
    │   │   └── ingestion.py      # coverage + live Elexon sync
    │   ├── services/
    │   │   ├── __init__.py
    │   │   ├── feature_extractor.py   # causal query + (24, 9) feature matrix
    │   │   ├── model_predictor.py     # vectorized .pkl ensemble inference
    │   │   ├── accuracy_estimator.py  # P10/P50/P90, expected accuracy, settlement audit
    │   │   ├── cutoff_advisor.py      # 8-cutoff policy + tranche recommendations
    │   │   └── elexon_ingestor.py     # live BMRS fetch, normalize, upsert, coverage
    │   └── tests/
    │       ├── __init__.py
    │       └── test_production_suite.py
    ├── data/
    │   ├── raw_windfor_stream.csv   # 83,940 raw WINDFOR revisions (Aug 2025 – Jan 2026)
    │   ├── raw_fuelhh_stream.csv    # 4,416 hourly FUELHH settlement actuals
    │   └── elexon_wind_full_audit.csv
    ├── Notebooks/
    │   └── optimze.ipynb            # model tuning workbench
    ├── docker-compose.yml           # PostgreSQL 16 (5433), API (8000), pgAdmin (5050)
    ├── Dockerfile.backend
    ├── requirements.txt
    └── wind_procurement_model.pkl   # serialized residual ensemble bundle
```


---

## How to start it

Quickest path is Docker Compose from the API folder — it brings up Postgres, the API, and pgAdmin together and seeds the DB on first boot.

```bash
cd Windward_API
docker compose up --build
```

- API: http://localhost:8000
- Swagger: http://localhost:8000/docs
- pgAdmin: http://localhost:5050 (`admin@gmail.com` / `admin`)

The first build pulls the scientific Python stack and seeds ~84k forecast rows, so give it a few minutes. After that, restarts are fast.

If you'd rather run the backend with plain Python:

```bash
cd Windward_API
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
python -m backend.db.seed_db
uvicorn backend.main:app --reload --port 8000
```

Without a `DATABASE_URL` it falls back to a local SQLite file at `Windward_API/data/wind_procurement.db`. To use Postgres, set `DATABASE_URL` before starting.

Then, in a second terminal, run the frontend:

```bash
cd Frontend
npm ci
npm run dev
```

Dashboard opens at http://localhost:8443. During dev the Vite proxy forwards `/api/...` to the backend on port 8000, so you usually don't need to set anything. If you want to call the API directly, add `Frontend/.env.local`:

```env
VITE_API_BASE_URL=http://localhost:8000
```

The app is already live at https://wisjy.vercel.app/, so local setup is only needed if you're changing the code.

---

## What the backend does

For any delivery date and Day-1 cutoff it:

- reads only the `WINDFOR` revisions published at or before that cutoff — no look-ahead;
- builds a `(24, 9)` trajectory feature matrix for the day;
- runs one vectorized residual-ensemble call instead of 24 separate predictions;
- returns `P10` / `P50` / `P90` bands, expected accuracy, and a settlement audit when actuals exist.

The model is trained on August–December 2025 only (29,376 samples). January 2026 is held out completely. On that unseen test set the ensemble cuts MAE from **2,064.52 MW** to **1,120.78 MW** (45.71%) and lifts R² from 0.6249 to 0.8792.

---

## API

Base URL: `https://windward-api.onrender.com/`

| Method | Endpoint | What it returns |
| --- | --- | --- |
| `GET` | `/api/v1/system/health` | DB status, row counts, model metadata, anti-leakage check |
| `GET` | `/api/v1/cutoffs/policy` | all eight cutoffs, recommended tranches, calibration source |
| `GET` | `/api/v1/forecast/day-ahead` | 24 hourly slots, daily KPIs, 8-cutoff comparison curve |
| `GET` | `/api/v1/benchmark/january-overview` | January 2026 out-of-sample benchmark across 5,952 hours |
| `GET` | `/api/v1/ingestion/coverage` | available delivery-date range in the DB |
| `POST` | `/api/v1/ingestion/sync-date` | fetch + upsert a delivery date from live Elexon BMRS |

Forecast query params:

| Param | Default | Notes |
| --- | --- | --- |
| `delivery_date` | `2026-01-15` | `YYYY-MM-DD` |
| `cutoff_time` | `15:30` | one of `02:30`–`22:30` UTC; bad values return 400 |
| `auto_ingest_if_missing` | `true` | fetches from Elexon automatically when the date isn't cached |

Example:

```bash
curl "https://windward-api.onrender.com/api/v1/forecast/day-ahead?delivery_date=2026-01-15&cutoff_time=15:30"
```

Every response carries `X-Process-Time-Ms`, which the dashboard shows in the chart footer.

---

## Tech stack

| Layer | Technology |
| --- | --- |
| Frontend | React 19, TypeScript, Vite 8, Tailwind CSS 4 |
| Charts | hand-built responsive SVG, no charting library |
| Fonts / styling | Inter + Lora, custom CSS design tokens |
| Backend | Python 3.11, FastAPI, Pydantic v2 |
| Data access | SQLAlchemy, psycopg2, parameterized SQL |
| ML | scikit-learn (`HistGradientBoostingRegressor`, `Ridge`), NumPy |
| Database | PostgreSQL 16 (SQLite fallback for local runs) |
| Data source | Elexon BMRS `WINDFOR` and `FUELHH` streams |
| Containers | Docker, Docker Compose |
| Hosting | Vercel (frontend), Render (backend + Postgres) |

---

## Used AI tools

- **Gemini Argon 4** — debugging and code optimization, mostly on the backend services and the vectorized inference path.
- **Gemini 3.8 Flash** — helped write and tighten this README.

---

## Testing

```bash
cd Windward_API

# with Docker
docker compose exec api python -m unittest backend.tests.test_production_suite -v

# without Docker (after activating the venv)
python -m unittest backend.tests.test_production_suite -v
```

You can also run the pipeline from the terminal:

```bash
python -m backend.cli_predict --date 2026-01-15 --cutoff 07:30
python -m backend.cli_predict --date 2026-01-15 --compare-cutoffs
python -m backend.main --endpoint health
python -m backend.main --endpoint forecast --date 2026-01-15 --cutoff 15:30
```


