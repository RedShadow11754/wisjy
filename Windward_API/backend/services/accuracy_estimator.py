"""
Ex-Ante Risk & Accuracy Estimator and Settlement Auditor (backend/services/accuracy_estimator.py).

Computes two distinct sets of accuracy and risk metrics for every 24-hour forecast:
  1. Ex-Ante Pre-Delivery Metrics (computed BEFORE delivery using ONLY the 9 features
     + December 2025 validation calibration stored in wind_procurement_model.pkl — ZERO leakage):
       - expected_error_mw
       - expected_accuracy_pct
       - p10_safe_floor_mw (90% confident firm wind capacity floor)
       - p50_ml_forecast_mw
       - p90_upper_bound_mw
       - confidence_tier
  2. Post-Delivery Realized Settlement Audit Metrics (when FUELHH actuals exist in DB):
       - actual_wind_mw
       - realized_ml_error_mw & realized_latest_error_mw
       - realized_ml_accuracy_pct & realized_latest_accuracy_pct
       - mw_error_saved_by_ml
"""

import math
from typing import Any, Dict, List, Optional


# Calibrated strictly on Aug–Dec 2025 (December 2025 Validation Holdout Set — ZERO Jan 2026 leakage)
DEC2025_VAL_CUTOFF_MAE_MW: Dict[str, float] = {
    "02:30": 1486.4,
    "04:30": 1458.2,
    "07:30": 1368.5,
    "09:30": 1349.1,
    "11:30": 1274.6,
    "15:30": 1218.4,
    "18:30": 1212.0,
    "22:30": 1239.5,
}
HIST_MEDIAN_STD_FORECAST_MW = 448.16
HIST_MEAN_ABS_REVISION_DELTA_MW = 418.52


def estimate_24h_accuracy_and_intervals(
    hourly_records: List[Dict[str, Any]],
    ml_predicted_mw_24: List[float],
    predicted_residual_mw_24: List[float],
    actuals_by_hour: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Attaches Ex-Ante Expected Accuracy %, P10/P50/P90 MW intervals, and (if available)
    Realized Settlement Audit metrics to each of the 24 hourly slots, and computes
    the daily aggregate KPI summary.
    """
    enriched_slots: List[Dict[str, Any]] = []

    sum_latest_mw = 0.0
    sum_ml_mw = 0.0
    sum_p10_mw = 0.0
    sum_p90_mw = 0.0
    sum_expected_err_mw = 0.0
    sum_expected_acc_pct = 0.0

    has_realized = False
    sum_actual_mw = 0.0
    sum_realized_ml_err = 0.0
    sum_realized_latest_err = 0.0
    sum_realized_ml_sq_err = 0.0
    sum_realized_latest_sq_err = 0.0
    sum_realized_ml_acc = 0.0
    sum_realized_latest_acc = 0.0
    p10_exceeded_count = 0
    in_p10_p90_band_count = 0

    for idx, rec in enumerate(hourly_records):
        feats = rec["features"]
        cutoff_utc = rec["cutoff_time_utc"]
        target_hr = rec["target_hour_utc"]

        latest_mw = float(feats["latest_forecast"])
        std_mw = float(feats["std_forecast"])
        abs_delta_mw = abs(float(feats["last_revision_delta"]))
        lead_hrs = float(feats["lead_time_hours"])

        ml_mw = float(ml_predicted_mw_24[idx])
        resid_adj_mw = float(predicted_residual_mw_24[idx])

        # --- 1. EX-ANTE PRE-DELIVERY ACCURACY & RISK ESTIMATION (Zero Future Data) ---
        base_val_mae = DEC2025_VAL_CUTOFF_MAE_MW.get(cutoff_utc, 1316.8)
        vol_ratio = max(0.40, min(2.20, std_mw / HIST_MEDIAN_STD_FORECAST_MW))
        shock_ratio = max(0.0, min(2.00, abs_delta_mw / HIST_MEAN_ABS_REVISION_DELTA_MW))

        expected_err_mw = round(base_val_mae * (0.72 + 0.20 * vol_ratio + 0.08 * shock_ratio), 1)
        denom_mw = max(1500.0, ml_mw)
        expected_acc_pct = round(max(50.0, min(98.0, 100.0 - (100.0 * expected_err_mw / denom_mw))), 1)

        half_width_80 = 1.45 * expected_err_mw
        p10_safe_floor_mw = round(max(0.0, ml_mw - half_width_80), 1)
        p50_ml_mw = round(ml_mw, 1)
        p90_upper_mw = round(min(18500.0, ml_mw + half_width_80), 1)

        if expected_acc_pct >= 89.0:
            conf_tier = "Very High Confidence (Tight Lock)"
        elif expected_acc_pct >= 84.0:
            conf_tier = "High Confidence (Standard Lock)"
        else:
            conf_tier = "Moderate Confidence (Wide Hedge)"

        sum_latest_mw += latest_mw
        sum_ml_mw += ml_mw
        sum_p10_mw += p10_safe_floor_mw
        sum_p90_mw += p90_upper_mw
        sum_expected_err_mw += expected_err_mw
        sum_expected_acc_pct += expected_acc_pct

        slot_payload: Dict[str, Any] = {
            "target_hour_utc": target_hr,
            "start_time_utc": rec["start_time_utc"],
            "publish_time_utc": rec["publish_time_utc"],
            "cutoff_time_utc": cutoff_utc,
            "lead_time_hours": lead_hrs,
            "latest_forecast_mw": round(latest_mw, 1),
            "ml_predicted_mw": p50_ml_mw,
            "ml_residual_adjustment_mw": round(resid_adj_mw, 1),
            "p10_safe_floor_mw": p10_safe_floor_mw,
            "p50_ml_forecast_mw": p50_ml_mw,
            "p90_upper_bound_mw": p90_upper_mw,
            "expected_error_mw": expected_err_mw,
            "expected_accuracy_pct": expected_acc_pct,
            "confidence_tier": conf_tier,
            "features": feats,
            "day1_revision_trajectory": rec["day1_revision_trajectory"],
        }

        # --- 2. POST-DELIVERY REALIZED SETTLEMENT AUDIT (If actuals exist in DB) ---
        act_row = actuals_by_hour.get(target_hr) if actuals_by_hour else None
        if act_row is not None:
            has_realized = True
            actual_mw = round(float(act_row["actual_wind_mw"]), 1)
            rolling_7d_mw = round(float(act_row["rolling_7d_wind_mw"]), 1)
            act_denom = max(500.0, actual_mw)

            real_ml_err = round(abs(actual_mw - ml_mw), 1)
            real_lat_err = round(abs(actual_mw - latest_mw), 1)
            real_ml_acc = round(max(0.0, 100.0 - (100.0 * real_ml_err / act_denom)), 1)
            real_lat_acc = round(max(0.0, 100.0 - (100.0 * real_lat_err / act_denom)), 1)
            mw_saved = round(real_lat_err - real_ml_err, 1)

            slot_payload.update(
                {
                    "actual_wind_mw": actual_mw,
                    "rolling_7d_wind_mw": rolling_7d_mw,
                    "realized_ml_error_mw": real_ml_err,
                    "realized_latest_error_mw": real_lat_err,
                    "realized_ml_accuracy_pct": real_ml_acc,
                    "realized_latest_accuracy_pct": real_lat_acc,
                    "mw_error_saved_by_ml": mw_saved,
                }
            )

            sum_actual_mw += actual_mw
            sum_realized_ml_err += real_ml_err
            sum_realized_latest_err += real_lat_err
            sum_realized_ml_sq_err += (actual_mw - ml_mw) ** 2
            sum_realized_latest_sq_err += (actual_mw - latest_mw) ** 2
            sum_realized_ml_acc += real_ml_acc
            sum_realized_latest_acc += real_lat_acc
            if actual_mw >= p10_safe_floor_mw:
                p10_exceeded_count += 1
            if p10_safe_floor_mw <= actual_mw <= p90_upper_mw:
                in_p10_p90_band_count += 1

        enriched_slots.append(slot_payload)

    n = max(1, len(enriched_slots))
    daily_kpis: Dict[str, Any] = {
        "hours_count": len(enriched_slots),
        "mean_latest_forecast_mw": round(sum_latest_mw / n, 1),
        "mean_ml_predicted_mw": round(sum_ml_mw / n, 1),
        "mean_p10_safe_floor_mw": round(sum_p10_mw / n, 1),
        "mean_p90_upper_bound_mw": round(sum_p90_mw / n, 1),
        "total_daily_ml_mwh": round(sum_ml_mw, 0),
        "total_daily_safe_firm_mwh": round(sum_p10_mw, 0),
        "expected_mae_mw": round(sum_expected_err_mw / n, 1),
        "expected_accuracy_pct": round(sum_expected_acc_pct / n, 1),
    }

    if has_realized:
        real_ml_mae = sum_realized_ml_err / n
        real_lat_mae = sum_realized_latest_err / n
        real_ml_rmse = math.sqrt(sum_realized_ml_sq_err / n)
        real_lat_rmse = math.sqrt(sum_realized_latest_sq_err / n)
        mae_saved = real_lat_mae - real_ml_mae
        mae_red_pct = (mae_saved / real_lat_mae * 100.0) if real_lat_mae > 0 else 0.0

        daily_kpis.update(
            {
                "mean_actual_wind_mw": round(sum_actual_mw / n, 1),
                "total_daily_actual_mwh": round(sum_actual_mw, 0),
                "realized_ml_mae_mw": round(real_ml_mae, 1),
                "realized_latest_mae_mw": round(real_lat_mae, 1),
                "realized_ml_rmse_mw": round(real_ml_rmse, 1),
                "realized_latest_rmse_mw": round(real_lat_rmse, 1),
                "realized_ml_accuracy_pct": round(sum_realized_ml_acc / n, 1),
                "realized_latest_accuracy_pct": round(sum_realized_latest_acc / n, 1),
                "mean_mw_error_saved_per_hour": round(mae_saved, 1),
                "realized_mae_reduction_pct": round(mae_red_pct, 1),
                "empirical_p10_safe_floor_hit_rate_pct": round(100.0 * p10_exceeded_count / n, 1),
                "empirical_p10_p90_band_coverage_pct": round(100.0 * in_p10_p90_band_count / n, 1),
            }
        )

    return {
        "daily_kpis": daily_kpis,
        "hourly_slots": enriched_slots,
    }
