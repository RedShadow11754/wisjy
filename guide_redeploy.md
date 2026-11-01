# Redeploy Guide — Persist Data in Render PostgreSQL

Your backend is live from a **Render Blueprint** (`render.yaml`). So far it has stored its data in
**SQLite inside the container filesystem**, which is wiped on every deploy, restart and free-tier
spin-down. Every restart therefore re-seeds from the bundled CSVs, and rows added by
`POST /api/v1/ingestion/sync-date` disappear.

This changes the Bluerint to provision a managed **PostgreSQL 16** instance and wire it into the API
automatically.

**No data migration is needed.** Every row is derived from the two CSVs that are already in the
image, and the new database is seeded from them on first boot. Nothing in the old SQLite file is
worth keeping.

> **Decide on the plan now.** Render's free Postgres is 1 GB and is **deleted 30 days after
> creation** (you get an email and a grace period first). Fine for a demo. If this must live longer,
> change the database's `plan:` in `render.yaml` before you sync — see [§7](#7-changing-the-database-plan).
> Everything else in this guide is identical either way.

---

## 0. What changes (already applied in this repo)

| File | Change | Why |
| --- | --- | --- |
| `render.yaml` | New `databases:` block (`windward-db`); `DATABASE_URL` injected via `fromDatabase`; `ALLOW_SQLITE_FALLBACK: "false"` | Blueprint provisions the database and keeps the connection string in sync. No env var to paste. |
| `backend/db/seed_db.py` | Boot seeding is no longer forced; dropping tables needs an explicit `--force` | The image runs the seeder on **every** start. Against a persistent database the old behaviour would `DROP` both tables and reload 88,356 rows on every deploy and every wake-up. |
| `backend/db/database.py` | Logs the real connection error to stderr; connect timeout 2 s → 10 s | The old code swallowed the error and quietly used SQLite — a "healthy" API writing to a doomed file. |
| `backend/config.py` | New `ALLOW_SQLITE_FALLBACK` flag | With `false`, an unreachable database fails the boot loudly instead of degrading silently. |

---

## 1. Push the change

```bash
git add -A
git commit -m "Persist backend data in managed Postgres"
git push origin main
```

If your Blueprint has **Auto Sync** enabled, the push starts the sync on its own. If not, go to
**Blueprints → your blueprint → Sync**.

---

## 2. Sync the Blueprint

On the blueprint page you should see exactly two effects:

```
+ windward-db      PostgreSQL · free · oregon     (new)
~ windward-api     Docker · new env vars          (updated)
```

Click **Sync**. Render then:

1. provisions the database `windward-db` in `oregon`, and
2. writes `DATABASE_URL` and `ALLOW_SQLITE_FALLBACK=false` onto `windward-api`, then redeploys it.

**That is the entire configuration step.** Nothing to paste — that is the payoff of using the
Blueprint. You do **not** need the database page's connection string at all, and you should not add
`DATABASE_URL` manually (the blueprint would overwrite it on the next sync).

> **If the first deploy fails**, the database was still provisioning when the container started.
> **Manual Deploy → Deploy latest commit** (or *Restart*) and it will come up. Look for
> `RuntimeError: ... ALLOW_SQLITE_FALLBACK is disabled` in the logs — that is this case, not a typo.

---

## 3. Watch the first boot

Render → `windward-api` → **Logs**:

```
[Database Seed Complete] {'status': 'seeded', 'engine': 'postgresql',
                          'raw_windfor_forecasts_count': 83940,
                          'raw_fuelhh_actuals_count': 4416}
Application startup complete.
```

| What | Cost | Notes |
| --- | --- | --- |
| Seed 83,940 + 4,416 rows into an empty Postgres | **13 s measured** against real PostgreSQL 16; expect **~15–60 s** on Render's shared CPU | One time only. |
| Model refit on import | **~58 s measured** | Every cold start — unrelated to this change (see `deploy.md` §7). |
| Later boots / wake-ups | **~1 s** to read row counts, returns `already_seeded` | The point of this change: no re-seed on restart. |

---

## 4. Verify

### 4.1 It is really Postgres

```bash
curl -s https://windward-api.onrender.com/api/v1/system/health
```

```json
"database": {
  "connected": true,
  "engine": "postgresql",
  "raw_windfor_forecasts_rows": 83940,
  "raw_fuelhh_actuals_rows": 4416,
  "unseen_january_2026_days": 31
}
```

`"engine": "postgresql"` is the proof. `"postgresql_compatible_sqlite"` means the fallback fired —
the log line names the reason.

```bash
curl -s https://windward-api.onrender.com/api/v1/ingestion/coverage
```

Expect `"database_engine": "postgresql"`, coverage `2025-08-01 … 2026-01-31`, and three splits
(`train_aug_oct`, `val_nov`, `test_dec`).

### 4.2 It survives a restart

This is the test that separates a real database from the old file. Write a date outside the seeded
window, restart, and ask for it again:

```bash
# 1. write
curl -s -X POST https://windward-api.onrender.com/api/v1/ingestion/sync-date \
  -H "Content-Type: application/json" \
  -d '{"delivery_date":"2026-02-10","force_live_fetch":true}'
```

**Render → `windward-api` → Manual Deploy → Restart Service**

```bash
# 2. read back, no live fetch
curl -s -X POST https://windward-api.onrender.com/api/v1/ingestion/sync-date \
  -H "Content-Type: application/json" \
  -d '{"delivery_date":"2026-02-10","force_live_fetch":false}'
```

`"status": "cached_in_database"` after the restart closes the loop. On SQLite this came back empty.
(If Elexon has no data for that date you get a 400 naming the API — use a recent past date instead.)

### 4.3 Frontend

Nothing changed — same URL, same JSON, same CORS. Confirm `Live · FastAPI backend` with a non-zero
response time at `https://<project>.vercel.app`, and that a date in coverage (e.g. `2026-01-15`)
still charts. Ignore the first request taking 1–2 min after a deploy; that is the model refit.

---

## 5. Redeploying later

| What changed | What to do |
| --- | --- |
| Backend or frontend code | Push to `main`. Boot logs `already_seeded` in ~1 s; **your data is untouched**. |
| `render.yaml` (env vars, plan, health check) | Push, then **Blueprints → Sync** if Auto Sync is off. |
| Backend env var (e.g. `CORS_ALLOW_ORIGINS`) | Dashboard → `windward-api` → Environment → Save (auto-redeploys). Blueprint vars with `sync: false` are meant to be set this way. |
| Frontend backend URL | Update `VITE_API_BASE_URL` in Vercel, then **Redeploy** (it is baked in at build time). |

The seeder is idempotent, so a deploy will never wipe the database. The one exception: if the row
counts are *below* 83,940 / 4,416 (a partially completed seed), it deletes both tables and reloads
them from the CSVs — losing any live-ingested dates.

---

## 6. Inspecting the database

Render → **`windward-db`** → **Connect** gives a `psql` command, or use the **Query** tab. From your
laptop, the **External** URL needs `?sslmode=require`:

```bash
psql "postgresql://windward:<password>@dpg-xxxxxxxx-a.oregon-postgres.render.com/wind_procurement?sslmode=require"
```

```sql
\dt
SELECT COUNT(*) FROM raw_windfor_forecasts;   -- 83940
SELECT COUNT(*) FROM raw_fuelhh_actuals;      -- 4416
SELECT split_set, COUNT(*) FROM raw_fuelhh_actuals GROUP BY split_set;
SELECT MAX(delivery_date) FROM raw_fuelhh_actuals;
```

The **Internal** URL (used by the service) has no port and no `sslmode` — that is correct; it runs
over Render's private network.

---

## 7. Changing the database plan

`plan: free` in `render.yaml` is what triggers the 30-day deletion. To avoid it, edit the plan
**in `render.yaml`, then sync** — do not change it in the dashboard. Blueprint-managed resources are
reconciled on every sync, so a dashboard-only edit is reverted (and can conflict on databases).
Pick the tier name from Render's pricing page; upgrading in place keeps the data.

---

## 8. Resetting the data

Run these in Render → `windward-api` → **Shell**:

```bash
# Non-destructive: create tables if missing, seed only if empty.
python3 -m backend.db.seed_db

# Destructive: DROP both tables and reload from the CSVs.
# Also deletes rows added by live sync.
python3 -m backend.db.seed_db --force
```

For a completely fresh start: delete `windward-db` in the dashboard, sync the blueprint again to
recreate it, and the next boot seeds the empty database. `fromDatabase` re-injects the new
connection string automatically.

---

## 9. Rollback to SQLite

Temporary debugging only. In `render.yaml`: remove the `DATABASE_URL` entry and set
`ALLOW_SQLITE_FALLBACK: "true"`, then sync. The service re-seeds `data/wind_procurement.db` on every
boot and health reports `postgresql_compatible_sqlite`. You lose all data on each restart.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Deploy fails right after the sync: `RuntimeError: PostgreSQL connection failed and ALLOW_SQLITE_FALLBACK is disabled` | Database still provisioning, or wrong region | Wait for `windward-db` to show **Available**, then redeploy. Check both resources are `oregon` in `render.yaml`. |
| Health shows `postgresql_compatible_sqlite` | Fallback fired | The log line gives the reason (`could not translate host name`, `password authentication failed`, `timeout expired`). Fix `render.yaml`, sync, redeploy. |
| `could not translate host name "dpg-xxxx-a"` | Database and service in different regions | Set both to the same `region:` in `render.yaml` and sync. |
| `password authentication failed` | `DATABASE_URL` set by hand with a stale password | Delete the manual variable in the dashboard and let `fromDatabase` manage it. |
| `too many connections` / `timeout expired` | Free database waking up, or connection pressure | Keep the service at **one instance** — the app opens one connection per request, there is no pool. |
| Health shows `0` rows, no `[Database Seed Complete]` | Seed failed on an earlier boot | Shell: `python3 -m backend.db.seed_db` and read the traceback. Confirm CSVs shipped: `ls -la /app/data`. |
| A duplicate `windward-api` appeared | A second blueprint was created for the same repo | Delete the duplicate and keep one blueprint. |
| Seed still takes minutes on every deploy | Deployed commit predates the `--force` change | Logs must say `already_seeded` after the first boot; redeploy the latest commit. |
| `psql` from laptop: `no pg_hba.conf entry ... SSL off` | External URL without `sslmode` | Append `?sslmode=require`. |

---

## 11. Limits worth knowing

* **Free Postgres: 1 GB, deleted 30 days after creation.** Paid tier or another provider if it must
  persist — see §7.
* **One connection per request, no pool.** `backend/db/database.py` opens and closes a psycopg2
  connection per request: fine for one instance and demo traffic, ~10–40 ms of overhead per request.
  Add a pool (or Render's PgBouncer on paid plans) before real load.
* **No backups on the free tier.** The committed CSVs are the real source of truth here.
* **No authentication.** `POST /api/v1/ingestion/sync-date` is writable by anyone with the URL — and
  now those writes survive restarts. Gate it before exposing the API more widely.
