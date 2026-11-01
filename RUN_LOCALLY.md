# Running Windward locally

Two processes:

| Piece | Folder | URL |
| --- | --- | --- |
| FastAPI backend | `elexon_wind_backend_docker` | http://localhost:8000 |
| React/Vite frontend | `Frontend` | http://localhost:8443 |

The frontend calls the backend at `/api/v1/...` on its own origin. Vite proxies
`/api` to `http://localhost:8000`, so no CORS or `.env` setup is required.

---

## 1. Start the backend

### Option A — Docker (recommended)

Requires Docker Desktop.

```powershell
cd D:\Wind_finale\elexon_wind_backend_docker
docker compose up -d --build
docker compose ps
```

The first start seeds PostgreSQL and runs the backend test suite before Uvicorn
launches, so it can take a few minutes. Wait until the API is healthy:

```powershell
Invoke-WebRequest http://localhost:8000/api/v1/system/health -UseBasicParsing
```

Services:

- API — http://localhost:8000 (docs at http://localhost:8000/docs)
- PostgreSQL — `localhost:5433` (`postgres` / `postgres`, database `wind_procurement`)
- pgAdmin — http://localhost:5050 (`admin@gmail.com` / `admin`)

Stop without deleting data:

```powershell
docker compose down
```

Stop and wipe the database volume:

```powershell
docker compose down -v
```

### Option B — no Docker (Python only)

Requires Python 3.11+.

```powershell
cd D:\Wind_finale\elexon_wind_backend_docker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

If PowerShell blocks the activation script, skip activation and call the venv
interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

When PostgreSQL is not reachable, the backend automatically falls back to a
SQLite replica at `backend\data\wind_procurement.db`, seeded from the CSVs in
`data\`. No extra configuration is needed. To use a specific database instead:

```powershell
$env:DATABASE_URL = "postgresql://postgres:postgres@localhost:5433/wind_procurement"
```

Check it is up:

```powershell
Invoke-WebRequest http://localhost:8000/api/v1/system/health -UseBasicParsing
```

---

## 2. Start the frontend

Requires Node.js 20+ and npm (tested with Node 24).

```powershell
cd D:\Wind_finale\Frontend
npm install
npm run dev
```

Open http://localhost:8443. The header badge shows `Live · FastAPI backend` plus
the response time when the API is reachable, and `API offline · retry` otherwise.

Production build:

```powershell
npm run build
npm run preview
```

If the dev server is already using 8443, preview on another port:

```powershell
$env:PORT = "8445"
npm run preview
```

### Pointing at a different backend

Vite proxy target (default `http://localhost:8000`):

```powershell
$env:VITE_API_PROXY_TARGET = "http://192.168.1.50:8000"
npm run dev
```

Or bypass the proxy and call an absolute API origin (the backend enables CORS
for all origins):

```powershell
$env:VITE_API_BASE_URL = "https://api.example.com"
npm run dev
```

For a deployed static build, either serve it behind a reverse proxy that maps
`/api` to the backend, or build with `VITE_API_BASE_URL` set.

---

## What is wired to the API

| UI element | Endpoint |
| --- | --- |
| Delivery-date bounds (min/max) | `GET /api/v1/ingestion/coverage` |
| 24-hour chart, KPI cards, selected-hour panel | `GET /api/v1/forecast/day-ahead?delivery_date=…&cutoff_time=…&auto_ingest_if_missing=true` |
| Delivery date / decision time | refetches the forecast |
| Refresh button | refetches the forecast |
| Actual series toggle | enabled only when the date has FUELHH actuals |

The page shows a live status badge, a response-time readout in the footer, and an
error banner with a Retry button if the backend is unreachable. The backend
serves coverage for `2025-08-01` through `2026-01-31`.

---

## Troubleshooting

- **`API offline · retry`** — confirm `http://localhost:8000/api/v1/system/health`
  returns 200, then press Refresh.
- **Port 8000 already in use** — stop the other process or change the API port and
  set `VITE_API_PROXY_TARGET` to match.
- **Port 8443 already in use** — the Vite config uses `strictPort`; stop the other
  process or run with `$env:PORT = "8444"`.
- **Docker build is slow** — the image installs scientific Python packages and the
  container runs the test suite at startup. Subsequent starts are fast.
- **Empty table for a date** — the date is outside the ingested coverage range.
