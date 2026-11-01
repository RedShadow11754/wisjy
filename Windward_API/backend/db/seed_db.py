#!/usr/bin/env python3
"""
Elexon Historical Stream Database Seeder (backend/db/seed_db.py).
Seeds:
  - `raw_windfor_forecasts`: Raw Elexon WINDFOR forecast revision stream
    (`start_time_utc`, `publish_time_utc`, `delivery_date`, `target_hour_utc`, `forecast_mw`)
    from `data/raw_windfor_stream.csv` (83,941 revisions across Aug 2025 – Jan 2026).
  - `raw_fuelhh_actuals`: Raw FUELHH half-hourly/hourly wind settlement stream
    from `data/raw_fuelhh_stream.csv` (4,416 hourly slots across Aug 2025 – Jan 2026).
"""

import argparse
import csv
import os
from typing import Any, Dict

from backend.config import DATA_DIR
from backend.db.database import DatabaseConnection

RAW_WINDFOR_CSV = os.path.join(DATA_DIR, "raw_windfor_stream.csv")
RAW_FUELHH_CSV = os.path.join(DATA_DIR, "raw_fuelhh_stream.csv")

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS raw_windfor_forecasts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_time_utc VARCHAR(20) NOT NULL,
        publish_time_utc VARCHAR(20) NOT NULL,
        delivery_date VARCHAR(10) NOT NULL,
        target_hour_utc VARCHAR(5) NOT NULL,
        forecast_mw DOUBLE PRECISION NOT NULL,
        UNIQUE (start_time_utc, publish_time_utc)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_windfor_causal_lookup
        ON raw_windfor_forecasts (delivery_date, publish_time_utc, target_hour_utc);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_windfor_start_pub
        ON raw_windfor_forecasts (start_time_utc, publish_time_utc);
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_fuelhh_actuals (
        start_time_utc VARCHAR(20) PRIMARY KEY,
        delivery_date VARCHAR(10) NOT NULL,
        month VARCHAR(7) NOT NULL,
        target_hour_utc VARCHAR(5) NOT NULL,
        split_set VARCHAR(24) NOT NULL,
        slot_00_mw DOUBLE PRECISION NOT NULL,
        slot_30_mw DOUBLE PRECISION NOT NULL,
        actual_wind_mw DOUBLE PRECISION NOT NULL,
        rolling_7d_wind_mw DOUBLE PRECISION NOT NULL,
        seasonal_regime_ratio DOUBLE PRECISION NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fuelhh_delivery_date
        ON raw_fuelhh_actuals (delivery_date, target_hour_utc);
    """,
]


def seed_database(force_reseed: bool = False) -> Dict[str, Any]:
    db = DatabaseConnection()
    try:
        if force_reseed:
            db.execute_query("DROP TABLE IF EXISTS raw_windfor_forecasts")
            db.execute_query("DROP TABLE IF EXISTS raw_fuelhh_actuals")

        for stmt in DDL_STATEMENTS:
            if db.engine_type == "postgresql":
                stmt_pg = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                db.execute_query(stmt_pg)
            else:
                db.execute_query(stmt)

        existing_wf = db.execute_query("SELECT COUNT(*) AS cnt FROM raw_windfor_forecasts")[0]["cnt"]
        existing_fh = db.execute_query("SELECT COUNT(*) AS cnt FROM raw_fuelhh_actuals")[0]["cnt"]

        if not force_reseed and existing_wf >= 83940 and existing_fh >= 4416:
            return {
                "status": "already_seeded",
                "engine": db.engine_type,
                "raw_windfor_forecasts_count": int(existing_wf),
                "raw_fuelhh_actuals_count": int(existing_fh),
            }

        if not os.path.exists(RAW_WINDFOR_CSV) or not os.path.exists(RAW_FUELHH_CSV):
            raise FileNotFoundError(
                f"Required raw Elexon stream CSV files not found ({RAW_WINDFOR_CSV}, {RAW_FUELHH_CSV})."
            )

        db.execute_query("DELETE FROM raw_windfor_forecasts")
        db.execute_query("DELETE FROM raw_fuelhh_actuals")

        windfor_rows = []
        with open(RAW_WINDFOR_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                windfor_rows.append(
                    (
                        r["start_time_utc"],
                        r["publish_time_utc"],
                        r["delivery_date"],
                        r["target_hour_utc"],
                        float(r["forecast_mw"]),
                    )
                )

        fuelhh_rows = []
        with open(RAW_FUELHH_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                fuelhh_rows.append(
                    (
                        r["start_time_utc"],
                        r["delivery_date"],
                        r["month"],
                        r["target_hour_utc"],
                        r["split_set"],
                        float(r["slot_00_mw"]),
                        float(r["slot_30_mw"]),
                        float(r["actual_wind_mw"]),
                        float(r["rolling_7d_wind_mw"]),
                        float(r["seasonal_regime_ratio"]),
                    )
                )

        if db.sqlite_conn is not None:
            cur = db.sqlite_conn.cursor()
            cur.executemany(
                """
                INSERT OR IGNORE INTO raw_windfor_forecasts (
                    start_time_utc, publish_time_utc, delivery_date, target_hour_utc, forecast_mw
                ) VALUES (?, ?, ?, ?, ?)
                """,
                windfor_rows,
            )
            cur.executemany(
                """
                INSERT OR REPLACE INTO raw_fuelhh_actuals (
                    start_time_utc, delivery_date, month, target_hour_utc, split_set,
                    slot_00_mw, slot_30_mw, actual_wind_mw, rolling_7d_wind_mw, seasonal_regime_ratio
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                fuelhh_rows,
            )
            db.sqlite_conn.commit()
        elif db.pg_conn is not None:
            import psycopg2.extras

            with db.pg_conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    """
                    INSERT INTO raw_windfor_forecasts (
                        start_time_utc, publish_time_utc, delivery_date, target_hour_utc, forecast_mw
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (start_time_utc, publish_time_utc) DO NOTHING
                    """,
                    windfor_rows,
                    page_size=2000,
                )
                psycopg2.extras.execute_batch(
                    cur,
                    """
                    INSERT INTO raw_fuelhh_actuals (
                        start_time_utc, delivery_date, month, target_hour_utc, split_set,
                        slot_00_mw, slot_30_mw, actual_wind_mw, rolling_7d_wind_mw, seasonal_regime_ratio
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (start_time_utc) DO NOTHING
                    """,
                    fuelhh_rows,
                    page_size=1000,
                )
            db.pg_conn.commit()

        final_wf = db.execute_query("SELECT COUNT(*) AS cnt FROM raw_windfor_forecasts")[0]["cnt"]
        final_fh = db.execute_query("SELECT COUNT(*) AS cnt FROM raw_fuelhh_actuals")[0]["cnt"]
        return {
            "status": "seeded",
            "engine": db.engine_type,
            "raw_windfor_forecasts_count": int(final_wf),
            "raw_fuelhh_actuals_count": int(final_fh),
        }
    finally:
        db.close()


if __name__ == "__main__":
    # The Docker image runs this module on every container start. With a persistent
    # database (Render Postgres, Neon, Supabase) a forced reseed would DROP and
    # reload both tables on every deploy and every wake-from-sleep, so force is
    # opt-in via `--force`:
    #     python3 -m backend.db.seed_db --force
    parser = argparse.ArgumentParser(description="Seed raw Elexon streams into the database.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Drop both tables and reload every row. Destructive: wipes rows added by live sync.",
    )
    args = parser.parse_args()

    res = seed_database(force_reseed=args.force)
    print(f"[Database Seed Complete] {res}", flush=True)
