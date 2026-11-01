"""
Live Elexon BMRS Stream Ingestor Service (backend/services/elexon_ingestor.py).

Provides on-demand synchronization of raw Elexon Balancing Mechanism Reporting Service
(BMRS) `WINDFOR` and `FUELHH` streams for any requested `delivery_date` (`YYYY-MM-DD`):
  1. Checks if the date is already present in `raw_windfor_forecasts` / `raw_fuelhh_actuals`.
  2. When live sync is requested or a date outside the pre-seeded historical window is queried,
     queries the official Elexon Insights Solution REST API:
       - GET https://data.elexon.co.uk/bmrs/api/v1/datasets/WINDFOR/stream
       - GET https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH/stream
  3. Parses, normalizes to 1-hour UTC slots, and idempotently upserts into PostgreSQL.
"""

import datetime
import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

from backend.config import ELEXON_API_BASE
from backend.db.database import DatabaseConnection


def _normalize_iso_z(ts_str: str) -> str:
    if not ts_str:
        return ""
    if ts_str.endswith("Z"):
        return ts_str
    if "+" in ts_str:
        dt = datetime.datetime.fromisoformat(ts_str).astimezone(datetime.timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return ts_str + "Z"


def fetch_live_bmrs_windfor(delivery_date: str, timeout_sec: int = 10) -> List[Tuple[str, str, str, str, float]]:
    """
    Fetches WINDFOR forecast revisions from Elexon BMRS published across [D-2 00:00Z .. D-1 23:59Z]
    targeting the 24 settlement hours of `delivery_date`.
    """
    deliv_dt = datetime.date.fromisoformat(delivery_date)
    pub_from = f"{(deliv_dt - datetime.timedelta(days=2)).isoformat()}T00:00:00Z"
    pub_to = f"{(deliv_dt - datetime.timedelta(days=1)).isoformat()}T23:59:59Z"

    params = urllib.parse.urlencode(
        {
            "publishDateTimeFrom": pub_from,
            "publishDateTimeTo": pub_to,
        }
    )
    url = f"{ELEXON_API_BASE}/WINDFOR/stream?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "UK-Wind-Procurement-Engine/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    records = payload if isinstance(payload, list) else payload.get("data", [])
    rows: List[Tuple[str, str, str, str, float]] = []
    seen = set()

    for item in records:
        start_iso = _normalize_iso_z(str(item.get("startTime", "")))
        pub_iso = _normalize_iso_z(str(item.get("publishTime", "")))
        gen_mw = item.get("generation")
        if not start_iso.startswith(delivery_date) or not pub_iso or gen_mw is None:
            continue
        target_hr = start_iso[11:16]
        key = (start_iso, pub_iso)
        if key in seen:
            continue
        seen.add(key)
        rows.append((start_iso, pub_iso, delivery_date, target_hr, round(float(gen_mw), 2)))

    rows.sort(key=lambda r: (r[3], r[1]))
    return rows


def fetch_live_bmrs_fuelhh(delivery_date: str, timeout_sec: int = 10) -> List[Tuple[str, str, str, str, str, float, float, float, float, float]]:
    """
    Fetches half-hourly FUELHH wind settlement records from Elexon BMRS for `delivery_date`
    and aggregates pairs of 30-minute slots (:00 and :30) into 1-hour average MW records.
    """
    params = urllib.parse.urlencode(
        {
            "settlementDateFrom": delivery_date,
            "settlementDateTo": delivery_date,
            "fuelType": "WIND",
        }
    )
    url = f"{ELEXON_API_BASE}/FUELHH/stream?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "UK-Wind-Procurement-Engine/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    records = payload if isinstance(payload, list) else payload.get("data", [])
    by_hour_slots: Dict[str, Dict[str, float]] = {}

    for item in records:
        if str(item.get("fuelType", "WIND")).upper() != "WIND":
            continue
        start_iso = _normalize_iso_z(str(item.get("startTime", "")))
        gen_mw = item.get("generation")
        if not start_iso.startswith(delivery_date) or gen_mw is None:
            continue
        hh_str = start_iso[11:13] + ":00"
        mm_str = start_iso[14:16]
        by_hour_slots.setdefault(hh_str, {})[mm_str] = float(gen_mw)

    if not by_hour_slots:
        return []

    hourly_actuals = []
    for h_idx in range(24):
        hr_str = f"{h_idx:02d}:00"
        slot_map = by_hour_slots.get(hr_str, {})
        if not slot_map:
            continue
        s00 = slot_map.get("00", slot_map.get("30", 0.0))
        s30 = slot_map.get("30", s00)
        act_mw = round((s00 + s30) / 2.0, 2)
        hourly_actuals.append((hr_str, round(s00, 2), round(s30, 2), act_mw))

    if not hourly_actuals:
        return []

    mean_day_mw = sum(x[3] for x in hourly_actuals) / len(hourly_actuals)
    month_str = delivery_date[:7]
    out_rows = []
    for hr_str, s00, s30, act_mw in hourly_actuals:
        start_iso = f"{delivery_date}T{hr_str}:00Z"
        out_rows.append(
            (
                start_iso,
                delivery_date,
                month_str,
                hr_str,
                "live_bmrs",
                s00,
                s30,
                act_mw,
                round(mean_day_mw, 2),
                1.0,
            )
        )
    return out_rows


def sync_delivery_date_from_elexon(
    db: DatabaseConnection,
    delivery_date: str,
    force_live_fetch: bool = False,
) -> Dict[str, Any]:
    """
    Synchronizes `WINDFOR` and `FUELHH` data for `delivery_date`.
    If data is already stored in PostgreSQL and `force_live_fetch=False`, returns the
    existing database row counts immediately. Otherwise fetches live from Elexon BMRS
    and upserts into PostgreSQL.
    """
    try:
        datetime.date.fromisoformat(delivery_date)
    except ValueError as exc:
        raise ValueError(f"Invalid delivery_date '{delivery_date}'. Expected YYYY-MM-DD.") from exc

    existing_wf = db.execute_query(
        "SELECT COUNT(*) AS cnt FROM raw_windfor_forecasts WHERE delivery_date = ?",
        (delivery_date,),
    )[0]["cnt"]
    existing_fh = db.execute_query(
        "SELECT COUNT(*) AS cnt FROM raw_fuelhh_actuals WHERE delivery_date = ?",
        (delivery_date,),
    )[0]["cnt"]

    if existing_wf > 0 and not force_live_fetch:
        return {
            "delivery_date": delivery_date,
            "status": "cached_in_database",
            "source": f"PostgreSQL ({db.engine_type})",
            "windfor_revisions_count": int(existing_wf),
            "fuelhh_hours_count": int(existing_fh),
            "live_api_called": False,
        }

    live_wf_rows: List[Tuple[str, str, str, str, float]] = []
    live_fh_rows: List[Tuple[str, str, str, str, str, float, float, float, float, float]] = []
    api_error = None

    try:
        live_wf_rows = fetch_live_bmrs_windfor(delivery_date)
        live_fh_rows = fetch_live_bmrs_fuelhh(delivery_date)
    except Exception as exc:
        api_error = str(exc)

    if live_wf_rows:
        if db.pg_conn is not None:
            import psycopg2.extras

            with db.pg_conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    """
                    INSERT INTO raw_windfor_forecasts (
                        start_time_utc, publish_time_utc, delivery_date, target_hour_utc, forecast_mw
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (start_time_utc, publish_time_utc)
                    DO UPDATE SET forecast_mw = EXCLUDED.forecast_mw
                    """,
                    live_wf_rows,
                    page_size=500,
                )
                if live_fh_rows:
                    psycopg2.extras.execute_batch(
                        cur,
                        """
                        INSERT INTO raw_fuelhh_actuals (
                            start_time_utc, delivery_date, month, target_hour_utc, split_set,
                            slot_00_mw, slot_30_mw, actual_wind_mw, rolling_7d_wind_mw, seasonal_regime_ratio
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (start_time_utc)
                        DO UPDATE SET
                            slot_00_mw = EXCLUDED.slot_00_mw,
                            slot_30_mw = EXCLUDED.slot_30_mw,
                            actual_wind_mw = EXCLUDED.actual_wind_mw
                        """,
                        live_fh_rows,
                        page_size=200,
                    )
            db.pg_conn.commit()
        elif db.sqlite_conn is not None:
            cur = db.sqlite_conn.cursor()
            cur.executemany(
                """
                INSERT OR REPLACE INTO raw_windfor_forecasts (
                    start_time_utc, publish_time_utc, delivery_date, target_hour_utc, forecast_mw
                ) VALUES (?, ?, ?, ?, ?)
                """,
                live_wf_rows,
            )
            if live_fh_rows:
                cur.executemany(
                    """
                    INSERT OR REPLACE INTO raw_fuelhh_actuals (
                        start_time_utc, delivery_date, month, target_hour_utc, split_set,
                        slot_00_mw, slot_30_mw, actual_wind_mw, rolling_7d_wind_mw, seasonal_regime_ratio
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    live_fh_rows,
                )
            db.sqlite_conn.commit()

    final_wf = db.execute_query(
        "SELECT COUNT(*) AS cnt FROM raw_windfor_forecasts WHERE delivery_date = ?",
        (delivery_date,),
    )[0]["cnt"]
    final_fh = db.execute_query(
        "SELECT COUNT(*) AS cnt FROM raw_fuelhh_actuals WHERE delivery_date = ?",
        (delivery_date,),
    )[0]["cnt"]

    if final_wf == 0:
        raise ValueError(
            f"No WINDFOR data found for delivery_date='{delivery_date}' in database or live Elexon BMRS API"
            + (f" ({api_error})" if api_error else ".")
        )

    return {
        "delivery_date": delivery_date,
        "status": "ingested_from_elexon_bmrs" if live_wf_rows else "verified_in_database",
        "source": "Elexon Insights Solution API (BMRS v1)" if live_wf_rows else f"PostgreSQL ({db.engine_type})",
        "windfor_revisions_count": int(final_wf),
        "fuelhh_hours_count": int(final_fh),
        "live_api_called": True,
    }


def get_database_coverage_summary(db: DatabaseConnection) -> Dict[str, Any]:
    wf_stats = db.execute_query(
        """
        SELECT
            COUNT(*) AS total_revisions,
            COUNT(DISTINCT delivery_date) AS distinct_days,
            MIN(delivery_date) AS min_delivery_date,
            MAX(delivery_date) AS max_delivery_date,
            MIN(publish_time_utc) AS min_publish_utc,
            MAX(publish_time_utc) AS max_publish_utc
        FROM raw_windfor_forecasts
        """
    )[0]
    fh_stats = db.execute_query(
        """
        SELECT
            COUNT(*) AS total_hourly_actuals,
            COUNT(DISTINCT delivery_date) AS distinct_days,
            MIN(delivery_date) AS min_delivery_date,
            MAX(delivery_date) AS max_delivery_date
        FROM raw_fuelhh_actuals
        """
    )[0]
    split_breakdown = db.execute_query(
        """
        SELECT
            split_set,
            MIN(delivery_date) AS start_date,
            MAX(delivery_date) AS end_date,
            COUNT(DISTINCT delivery_date) AS days_count,
            COUNT(*) AS hours_count
        FROM raw_fuelhh_actuals
        GROUP BY split_set
        ORDER BY MIN(delivery_date) ASC
        """
    )
    return {
        "database_engine": db.engine_type,
        "windfor_stream": {
            "total_revisions": int(wf_stats["total_revisions"]),
            "distinct_delivery_days": int(wf_stats["distinct_days"]),
            "min_delivery_date": wf_stats["min_delivery_date"],
            "max_delivery_date": wf_stats["max_delivery_date"],
            "min_publish_utc": wf_stats["min_publish_utc"],
            "max_publish_utc": wf_stats["max_publish_utc"],
        },
        "fuelhh_stream": {
            "total_hourly_actuals": int(fh_stats["total_hourly_actuals"]),
            "distinct_delivery_days": int(fh_stats["distinct_days"]),
            "min_delivery_date": fh_stats["min_delivery_date"],
            "max_delivery_date": fh_stats["max_delivery_date"],
        },
        "chronological_splits": split_breakdown,
    }
