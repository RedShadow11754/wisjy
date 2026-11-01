# Deployment Guide — Vercel (frontend) + Render (backend)

End-to-end instructions for shipping the UK Day-Ahead Wind Procurement app:

| Piece | Host | Repo path | Resulting URL |
| --- | --- | --- | --- |
| React + Vite dashboard | **Vercel** | `Frontend/` | `https://<project>.vercel.app` |
| FastAPI engine + model + data | **Render** (Docker) | `Windward_API/` | `https://<service>.onrender.com` |

```
Browser
  │  HTTPS  GET https://<project>.vercel.app            → static React bundle (Vercel CDN)
  │  HTTPS  GET https://<service>.onrender.com/api/v1/… → FastAPI (Render, Docker)
  ▼
FastAPI ──► SQLite replica (auto-seeded from data/*.csv)
        ──► wind_procurement_model.pkl (refit on first import)
        ──► Elexon BMRS API (on-demand live ingest for uncached dates)
```

**Deploy the backend first** — the frontend build bakes in the backend URL.

---

## 0. Pre-flight changes already applied to this repo

These were required for the two hosts to work; nothing else in the app changed.

| File | Change | Why it was needed |
| --- | --- | --- |
| `Windward_API/Dockerfile.backend` | `CMD` now binds `--port ${PORT:-8000}` and no longer runs the Python test suite before Uvicorn | Render injects `$PORT` and expects the app to bind it. Running the ~full test suite on every boot delays/breaks the health check. |
| `Windward_API/backend/main.py` | CORS origins read from `CORS_ALLOW_ORIGINS` (comma-separated, default `*`); added `expose_headers=["X-Process-Time-Ms"]` | Frontend and API are now on **different origins**. Without the expose header the browser hides `X-Process-Time-Ms`, so the response-time readout silently shows `0`. |
| `Windward_API/.dockerignore` | new | Keeps `__pycache__/`, `.venv/`, stale `data/*.db` and zip bundles out of the image build context. |
| `Frontend/vercel.json` | new | Pins install/build/output settings (`npm ci`, `npm run build`, `dist`) and adds an SPA fallback. Repo has **both** `package-lock.json` and `pnpm-lock.yaml`, so the explicit install command removes guesswork. |
| `render.yaml` | new | Blueprint for the backend service (Docker context, health check path, env vars). |
| `RUN_LOCALLY.md` | note about the removed boot-time test run | Keeps local docs truthful. |

Local `docker compose up` behaviour is unchanged: with no `PORT` set the API still listens on `8000`.

---

## 1. Push the project to GitHub

Both hosts deploy from a Git repository.

```bash
git add -A
git commit -m "Add deployment configuration for Vercel + Render"
git push origin main
```

> The upload is ~11 MB (`data/raw_windfor_stream.csv` 5.5 MB, `data/elexon_wind_full_audit.csv` 4.6 MB). Those CSVs are **required at runtime** — Render re-seeds the database from them on every boot, and the model re-fits from `elexon_wind_full_audit.csv`. Keep them in the repo; do not gitignore them.

---

## 2. Part 1 — Deploy the backend on Render

### 2.1 Create the web service

**Option A — Blueprint (fastest, uses `render.yaml`)**

1. Render Dashboard → **New +** → **Blueprint**.
2. Connect the GitHub repo.
3. Render reads `render.yaml`, shows one service named `windward-api`.
4. Click **Apply**. It will prompt for `CORS_ALLOW_ORIGINS` — **leave it empty for now** (empty means "allow any origin", which you will tighten in Part 3).

**Option B — manual web service**

Render Dashboard → **New +** → **Web Service** → connect the repo, then use exactly:

| Field | Value |
| --- | --- |
| Name | `windward-api` |
| Language / Runtime | **Docker** |
| Branch | `main` |
| Root Directory | *(leave empty)* |
| Dockerfile Path | `Windward_API/Dockerfile.backend` |
| Docker Build Context Directory | `Windward_API` |
| Instance Type | `Free` (or `Starter` for always-on — see §7) |
| Health Check Path | `/api/v1/system/health` |
| Docker Command | *(leave empty — the image's `CMD` is correct)* |

Environment variables (add these in the **Environment** tab):

| Key | Value |
| --- | --- |
| `MODEL_PKL_PATH` | `/app/wind_procurement_model.pkl` |
| `ELEXON_API_BASE` | `https://data.elexon.co.uk/bmrs/api/v1/datasets` |
| `CORS_ALLOW_ORIGINS` | *(empty for now; set in Part 3)* |
| `DATABASE_URL` | **omit it** unless you provision Postgres (§2.5) |

Do **not** set `PORT` — Render provides it.

### 2.2 What happens on first boot

1. `pip install` of the scientific stack (numpy, pandas, scikit-learn, scipy) — **5–10 min** for the first build; later builds are cached.
2. `python3 -m backend.db.seed_db` creates the tables and inserts **83,940 WINDFOR revisions** + **4,416 FUELHH hourly actuals** from the bundled CSVs into `data/wind_procurement.db` (SQLite). Expect roughly **1–3 min**.
3. Uvicorn starts. Importing the app loads `wind_procurement_model.pkl`; the shipped artifact contains no fitted scikit-learn estimators, so the service **re-fits** `HistGradientBoosting` + `Ridge` on the 29,376 Aug–Dec 2025 rows from `elexon_wind_full_audit.csv` and caches the refit into the `.pkl`. This step alone measured **~58 s on a development machine**; expect it to be slower on Render's shared free CPU. Budget a few minutes for the first boot.

Render only routes traffic once the health check passes, so a slow first boot is expected and harmless. Watch the **Logs** tab; you should see `[Database Seed Complete]` followed by `Application startup complete`.

### 2.3 Note the URL

Copy the service URL from the dashboard, e.g. `https://windward-api.onrender.com`. You need it in Part 2.

### 2.4 Verify the API

```bash
# Health + row counts + model metadata
curl -s https://windward-api.onrender.com/api/v1/system/health

# 24-hour forecast (24 hourly slots, 8 cutoff comparisons)
curl -s "https://windward-api.onrender.com/api/v1/forecast/day-ahead?delivery_date=2026-01-15&cutoff_time=15:30"
```

Expected in the health payload: `"status": "healthy"`, `"engine": "postgresql_compatible_sqlite"`, `"raw_windfor_forecasts_rows": 83940`, `"raw_fuelhh_actuals_rows": 4416`, and a non-empty `execution_engine`.

Also open **`https://windward-api.onrender.com/docs`** — Swagger UI must load.

### 2.5 About the database (read this before go-live)

With no `DATABASE_URL`, `backend/db/database.py` falls back to SQLite at `data/wind_procurement.db`. On Render:

* The filesystem is **ephemeral**: a redeploy/restart wipes it, and the image `CMD` re-seeds it automatically. Your data is derived from the committed CSVs, so nothing is lost — you just pay the 1–3 min seed again.
* Writes from live Elexon ingest (`POST /api/v1/ingestion/sync-date`) live only until the next restart.
* SQLite writes are safe here because the API is a single instance; do not scale the service above one instance on SQLite.

**To use a managed Postgres instead** (needed if you want ingested dates to survive restarts):

1. Create a database — Render Postgres, or a free Neon/Supabase instance. Render's free Postgres plans are time-limited, so a free Neon database is often the pragmatic choice for a long-lived demo.
2. Copy the **internal** connection string (Render Postgres) or the pooled URI (Neon). It must start with `postgresql://`.
3. Add it to the service as `DATABASE_URL` and redeploy. The app seeds Postgres from the same CSVs on boot; `engine` in the health payload flips to `postgresql`.
4. If the connection drops at any point, the app silently falls back to SQLite — `engine` in `/api/v1/system/health` is how you confirm which backend is live.

---

## 3. Part 2 — Deploy the frontend on Vercel

### 3.1 Import the project

1. Vercel Dashboard → **Add New…** → **Project** → import the GitHub repo.
2. Set **Root Directory** to **`Frontend`** (critical — the repo root is a monorepo and has no `package.json`).
3. Framework Preset: **Vite** (auto-detected). `Frontend/vercel.json` supplies install (`npm ci`), build (`npm run build`) and output (`dist`) settings; leave the override fields empty unless you want to change them.
4. Add the environment variable:

| Key | Value | Environments |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `https://windward-api.onrender.com` | Production, Preview, Development |

Rules for this value: **no trailing slash**, include `https://`. The frontend calls `${VITE_API_BASE_URL}/api/v1/...`.

5. **Deploy**.

`VITE_API_BASE_URL` is inlined at build time, so changing the backend URL later requires a **redeploy** (Deployments → ⋯ → Redeploy), not just an env var edit.

### 3.2 Verify the frontend

1. Open `https://<project>.vercel.app`.
2. The header badge should read **`Live · FastAPI backend`** with a response time. If it reads `API offline · retry`, go to §6.
3. DevTools → Network: the `day-ahead` request must go to `windward-api.onrender.com` (not to the Vercel domain) and return **200**.
4. Confirm the footer response time is a real number (e.g. `14.82`) — `0` means `X-Process-Time-Ms` is not being exposed, i.e. the backend is still running the pre-fix image.
5. Load a date inside the seeded coverage (`2025-08-01` … `2026-01-31`, e.g. `2026-01-15`) so the chart, KPI cards and settlement overlay have data.

---

## 4. Part 3 — Lock CORS down to your frontend

The API currently accepts any origin. Once the frontend URL is known:

1. Render → `windward-api` → **Environment** → set
   `CORS_ALLOW_ORIGINS` = `https://<project>.vercel.app`
   (comma-separate to add preview domains or a custom domain: `https://a.vercel.app,https://wind.example.com`).
2. **Save** → the service redeploys automatically.
3. Reload the frontend and re-check the Network tab for CORS errors.

> Vercel **preview** deployments get a new URL per branch/commit. If you use them, add the whole stable alias or use `*` on a private demo. Origin matching is exact — no wildcards inside a listed origin.

CORS does not affect `curl`, Swagger UI, or server-to-server calls; it is purely a browser rule.

---

## 5. Post-deploy checklist

- [ ] `GET /api/v1/system/health` → `healthy`, 83940 / 4416 rows
- [ ] `GET /docs` renders on the Render URL
- [ ] `GET /api/v1/forecast/day-ahead?delivery_date=2026-01-15&cutoff_time=15:30` → `hourly_slots.length === 24`, `day_cutoff_comparison.length === 8`
- [ ] `GET /api/v1/ingestion/coverage` → max date `2026-01-31`
- [ ] Invalid cutoff (`cutoff_time=08:00`) → **HTTP 400** with a `detail` message
- [ ] Frontend badge shows `Live · FastAPI backend`; footer shows a non-zero response time
- [ ] No CORS errors in the browser console; requests go to `*.onrender.com`
- [ ] `CORS_ALLOW_ORIGINS` set to the Vercel URL

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Render deploy succeeds, then "port scan timeout" / no open port | Image not binding `$PORT` | Confirm the deployed revision uses the updated `Dockerfile.backend` (`--port ${PORT:-8000}`) and that Docker Command is empty. |
| Render health check fails at boot | Seed or model refit still running | Wait for the boot logs to reach `Application startup complete`; the check retries. Raise the health check grace if needed. |
| Frontend shows `API offline · retry` | `VITE_API_BASE_URL` missing/typo'd, or backend asleep | Check DevTools → Network. On the free tier the backend sleeps after 15 min idle and takes ~50–90 s to wake; press **Refresh**. |
| Requests go to `https://<project>.vercel.app/api/...` → 404 | `VITE_API_BASE_URL` not set at build time | Set it, then **redeploy** (env is baked into the bundle). |
| Console: `blocked by CORS policy` | `CORS_ALLOW_ORIGINS` doesn't exactly match the page origin | Use the full `https://…` origin, no trailing slash; or clear the var to allow all. |
| Footer response time is `0` | `X-Process-Time-Ms` not exposed | Backend needs the `expose_headers` change; redeploy it. |
| Chart empty for a date | Date outside `2025-08-01 … 2026-01-31` and Elexon has no live data | Pick a date in coverage, or `POST /api/v1/ingestion/sync-date` for a recent date. |
| `500` on health right after boot | Seed finished but WRITE failing (read-only/locked DB) | Check logs for `sqlite3.OperationalError`; restart the service. |
| Render build fails at `pip install` | Native wheel build (rare, usually `psycopg2`) | Logs show the failing package; `libpq-dev`/`gcc` are already installed in the Dockerfile — retry the build to rule out a transient index error. |
| Every deploy re-seeds for minutes | Expected with SQLite | Use a managed Postgres (§2.5) to avoid the re-seed cost. |

---

## 7. Limits and known constraints

**Vercel Hobby (free)** — plenty for this static bundle (~76 kB gzipped JS). No serverless functions are used, so there is no per-invocation cost. Function/timeout limits are irrelevant here.

**Render free instance** — 512 MB RAM, shared CPU, and the service **spins down after ~15 minutes of inactivity**; the first request afterwards pays the wake-up cost. Two consequences specific to this app:

* Cold requests can take **1.5–2.5 min**. Two costs stack on every cold start: the SQLite seed (~1–3 min is for the very first boot, faster after) and the model refit on import — measured at **~58 s on a dev machine**, since the shipped `.pkl` has no fitted estimators and re-fits from the 4.6 MB audit CSV every time.
* A `Starter` instance (~$7/mo) removes the spin-down and is the simplest fix if you demo to traders.

To keep a free instance warm, add an uptime pinger (e.g. a GitHub Actions cron hitting `/api/v1/system/health` every 10 min). Note this consumes free-tier hours.

**Other notes**

* There is **no authentication** on the API. `/api/v1/ingestion/sync-date` will call Elexon on demand, so anyone with the URL can trigger ingest work. Add an API key or platform-level auth before exposing this publicly for real.
* Keep the API at **one instance** while it uses SQLite.
* `data/elexon_wind_full_audit.csv` must stay in the image — the model re-fit depends on exactly 29,376 Aug–Dec rows in it.
* Optional hardening: a custom domain on Vercel (`Settings → Domains`), then add it to `CORS_ALLOW_ORIGINS`.

---

## 8. Redeploying later

| Change | What to do |
| --- | --- |
| Frontend code | Push to `main` → Vercel auto-deploys. |
| Backend code / model / CSVs | Push to `main` → Render rebuilds and re-seeds automatically (`autoDeploy: true`). |
| Backend URL changed | Update `VITE_API_BASE_URL` in Vercel → **Redeploy**. |
| Frontend URL changed | Update `CORS_ALLOW_ORIGINS` in Render → save (auto-redeploys). |
| Env var only | Render: save → auto-redeploy. Vercel: save → manual **Redeploy** needed for `VITE_*` vars. |

To deploy the backend locally exactly as Render runs it:

```bash
cd Windward_API
docker build -f Dockerfile.backend -t windward-api .
docker run --rm -p 8000:8000 -e PORT=8000 windward-api
```
