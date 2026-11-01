"""
Causal 24-Hour Trajectory Feature Extractor (backend/services/feature_extractor.py).

Queries the raw Elexon forecast revision table `raw_windfor_forecasts`
(`start_time_utc`, `publish_time_utc`, `delivery_date`, `target_hour_utc`, `forecast_mw`)
for all revisions published at or before the chosen Day-1 cutoff timestamp
(`publish_time_utc <= cutoff_ts`), and computes all 9 trajectory features
dynamically in memory for each of the 24 settlement hours (00:00 to 23:00 UTC).
"""

import datetime
import math
from typing import Any, Dict, List, Tuple

from backend.config import EWMA_ALPHA, FEATURE_COLUMNS, VALID_CUTOFFS_UTC
from backend.db.database import DatabaseConnection


def parse_iso_utc(ts_str: str) -> datetime.datetime:
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.datetime.fromisoformat(ts_str).astimezone(datetime.timezone.utc)


def compute_9_features_from_revisions(
    revisions_mw: List[float],
    lead_time_hours: float,
    publish_timestamps_utc: List[str] = None,
) -> Dict[str, float]:
    """
    Computes the 9 forecast trajectory features dynamically from the chronological
    revision vector [P_1, P_2, ..., P_N] published up to Day-1 cutoff T_c.
    """
    n = len(revisions_mw)
    if n == 0:
        raise ValueError("Cannot extract features from an empty revision sequence.")

    latest_f = float(revisions_mw[-1])
    first_f = float(revisions_mw[0])

    # 3. Exponentially Weighted Moving Average (alpha = 0.40)
    ewma_f = first_f
    for val in revisions_mw[1:]:
        ewma_f = EWMA_ALPHA * float(val) + (1.0 - EWMA_ALPHA) * ewma_f

    # 4 & 5. Mean and population standard deviation across revisions
    mean_f = sum(revisions_mw) / n
    variance = sum((v - mean_f) ** 2 for v in revisions_mw) / n
    std_f = math.sqrt(max(0.0, variance))

    # 6. Last revision step change (P_N - P_{N-1})
    last_delta = float(revisions_mw[-1] - revisions_mw[-2]) if n >= 2 else 0.0

    # 7. Overall trend slope (MW per hour of revision span, or per step if timestamps omitted)
    if n >= 2:
        if publish_timestamps_utc and len(publish_timestamps_utc) == n:
            t0 = parse_iso_utc(publish_timestamps_utc[0])
            tN = parse_iso_utc(publish_timestamps_utc[-1])
            span_hours = (tN - t0).total_seconds() / 3600.0
            slope = (latest_f - first_f) / max(1.0, span_hours)
        else:
            x_mean = (n - 1) / 2.0
            num = sum((i - x_mean) * (revisions_mw[i] - mean_f) for i in range(n))
            den = sum((i - x_mean) ** 2 for i in range(n))
            slope = num / den if den > 0 else 0.0
    else:
        slope = 0.0

    return {
        "latest_forecast": round(latest_f, 2),
        "first_forecast": round(first_f, 2),
        "ewma_forecast": round(ewma_f, 2),
        "mean_forecast": round(mean_f, 2),
        "std_forecast": round(std_f, 2),
        "last_revision_delta": round(last_delta, 2),
        "overall_trend_slope": round(slope, 4),
        "prediction_count": float(n),
        "lead_time_hours": round(float(lead_time_hours), 2),
    }


def extract_24h_features_for_cutoff(
    db: DatabaseConnection,
    delivery_date: str,
    cutoff_time_utc: str,
) -> Tuple[List[List[float]], List[Dict[str, Any]], str]:
    """
    Queries `raw_windfor_forecasts` for all revisions targeting `delivery_date`
    where `publish_time_utc <= cutoff_publish_ts`, and computes all 9 features
    for all 24 hours (00:00 to 23:00 UTC), returning the (24, 9) matrix.
    """
    if cutoff_time_utc not in VALID_CUTOFFS_UTC:
        raise ValueError(
            f"Invalid cutoff_time_utc '{cutoff_time_utc}'. Must be one of {VALID_CUTOFFS_UTC}."
        )

    try:
        deliv_dt = datetime.date.fromisoformat(delivery_date)
    except ValueError as exc:
        raise ValueError(f"Invalid delivery_date '{delivery_date}'. Expected YYYY-MM-DD.") from exc

    day_minus_1 = deliv_dt - datetime.timedelta(days=1)
    cutoff_publish_ts = f"{day_minus_1.isoformat()}T{cutoff_time_utc}:00Z"

    sql = """
        SELECT
            start_time_utc,
            publish_time_utc,
            delivery_date,
            target_hour_utc,
            forecast_mw
        FROM raw_windfor_forecasts
        WHERE delivery_date = ?
          AND publish_time_utc <= ?
        ORDER BY target_hour_utc ASC, publish_time_utc ASC
    """
    rows = db.execute_query(sql, (delivery_date, cutoff_publish_ts))
    if not rows:
        raise ValueError(
            f"No WINDFOR revision records found for delivery_date='{delivery_date}' up to cutoff '{cutoff_publish_ts}'."
        )

    by_hour: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        hr = r["target_hour_utc"]
        by_hour.setdefault(hr, []).append(r)

    X_day_24x9: List[List[float]] = []
    hourly_records: List[Dict[str, Any]] = []

    for h_idx in range(24):
        hr_str = f"{h_idx:02d}:00"
        hr_revs = by_hour.get(hr_str, [])
        if not hr_revs:
            # BST D-2 boundary fallback (anchors to nearest earlier hour on the same delivery_date)
            for fb_h in range(h_idx - 1, -1, -1):
                fb_revs = by_hour.get(f"{fb_h:02d}:00", [])
                if fb_revs:
                    hr_revs = fb_revs
                    break
        if not hr_revs:
            raise ValueError(f"Missing revisions for {delivery_date} hour {hr_str} at cutoff {cutoff_time_utc}")

        target_iso = f"{delivery_date}T{hr_str}:00Z"
        target_dt = parse_iso_utc(target_iso)
        latest_pub_iso = hr_revs[-1]["publish_time_utc"]
        latest_pub_dt = parse_iso_utc(latest_pub_iso)

        lead_hrs = (target_dt - latest_pub_dt).total_seconds() / 3600.0
        revisions_mw = [float(r["forecast_mw"]) for r in hr_revs]
        pub_timestamps = [str(r["publish_time_utc"]) for r in hr_revs]

        feats_dict = compute_9_features_from_revisions(
            revisions_mw=revisions_mw,
            lead_time_hours=lead_hrs,
            publish_timestamps_utc=pub_timestamps,
        )

        feature_vec = [float(feats_dict[col]) for col in FEATURE_COLUMNS]
        X_day_24x9.append(feature_vec)

        revision_trajectory = [
            {
                "publish_time_utc": r["publish_time_utc"],
                "forecast_mw": round(float(r["forecast_mw"]), 1),
            }
            for r in hr_revs
        ]

        hourly_records.append(
            {
                "delivery_date": delivery_date,
                "target_hour_utc": hr_str,
                "start_time_utc": target_iso,
                "publish_time_utc": latest_pub_iso,
                "cutoff_time_utc": cutoff_time_utc,
                "features": feats_dict,
                "day1_revision_trajectory": revision_trajectory,
            }
        )

    return X_day_24x9, hourly_records, cutoff_publish_ts
