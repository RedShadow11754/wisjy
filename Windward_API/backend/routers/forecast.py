"""
Day-Ahead 24-Hour Forecast & Accuracy Router (GET /api/v1/forecast/day-ahead).

Executes the 4-stage production pipeline for any requested (delivery_date, cutoff_time):
  1. PostgreSQL Causal Query: Fetches all WINDFOR revisions for the 24 hours of
     `delivery_date` published at or before `Day-1 cutoff_time` (auto-ingesting from
     live Elexon BMRS if the requested date is not yet cached in PostgreSQL).
  2. Feature Extractor ((24, 9) Matrix): Computes the 9 trajectory features for
     hours 00:00 to 23:00 UTC.
  3. Vectorized .pkl Model Inference: Feeds the (24, 9) array into
     `wind_procurement_model.pkl` in one batch call to obtain the 24 hourly predictions.
  4. Accuracy Estimator: Attaches `expected_accuracy_pct`, `expected_error_mw`,
     and `p10_safe_floor_mw` (plus realized settlement comparison when actuals exist).
"""

from typing import Any, Dict

from backend.config import VALID_CUTOFFS_UTC
from backend.db.database import DatabaseConnection
from backend.services.accuracy_estimator import estimate_24h_accuracy_and_intervals
from backend.services.cutoff_advisor import get_cutoff_metadata
from backend.services.elexon_ingestor import sync_delivery_date_from_elexon
from backend.services.feature_extractor import extract_24h_features_for_cutoff
from backend.services.model_predictor import MODEL_PREDICTOR


def build_day_ahead_forecast_payload(
    db: DatabaseConnection,
    delivery_date: str = "2026-01-15",
    cutoff_time: str = "15:30",
    auto_ingest_if_missing: bool = True,
) -> Dict[str, Any]:
    if auto_ingest_if_missing:
        cnt = db.execute_query(
            "SELECT COUNT(*) AS cnt FROM raw_windfor_forecasts WHERE delivery_date = ?",
            (delivery_date,),
        )[0]["cnt"]
        if int(cnt) == 0:
            sync_delivery_date_from_elexon(db, delivery_date, force_live_fetch=True)

    # 1 & 2. Query PostgreSQL up to cutoff timestamp and extract the (24, 9) feature matrix
    X_day_24x9, hourly_records, cutoff_publish_ts = extract_24h_features_for_cutoff(
        db=db,
        delivery_date=delivery_date,
        cutoff_time_utc=cutoff_time,
    )

    # 3. Vectorized .pkl Model Inference on the (24, 9) matrix in one batch call
    ml_pred_24, resid_adj_24, hgb_res_24, ridge_res_24 = MODEL_PREDICTOR.predict_24h_vectorized(
        X_day_24x9
    )

    # Fetch FUELHH actuals for `delivery_date` (for post-delivery backtest comparison)
    actual_rows = db.execute_query(
        """
        SELECT target_hour_utc, actual_wind_mw, rolling_7d_wind_mw, seasonal_regime_ratio, split_set
        FROM raw_fuelhh_actuals
        WHERE delivery_date = ?
        ORDER BY target_hour_utc ASC
        """,
        (delivery_date,),
    )
    actuals_by_hour = {r["target_hour_utc"]: r for r in actual_rows}

    # 4. Compute Ex-Ante Expected Accuracy %, P10/P50/P90 MW intervals, and Realized Audit
    evaluated = estimate_24h_accuracy_and_intervals(
        hourly_records=hourly_records,
        ml_predicted_mw_24=ml_pred_24,
        predicted_residual_mw_24=resid_adj_24,
        actuals_by_hour=actuals_by_hour,
    )

    # 5. Compute the 8-Cutoff Comparison Curve for this specific `delivery_date`
    day_cutoff_comparison = []
    for c_utc in VALID_CUTOFFS_UTC:
        X_c, recs_c, _ = extract_24h_features_for_cutoff(db, delivery_date, c_utc)
        pred_c, res_c, _, _ = MODEL_PREDICTOR.predict_24h_vectorized(X_c)
        eval_c = estimate_24h_accuracy_and_intervals(recs_c, pred_c, res_c, actuals_by_hour)
        kpis_c = eval_c["daily_kpis"]
        meta_c = get_cutoff_metadata(c_utc)

        day_cutoff_comparison.append(
            {
                "cutoff_time_utc": c_utc,
                "is_selected": c_utc == cutoff_time,
                "is_recommended": meta_c["is_recommended"],
                "is_primary_sweet_spot": meta_c["is_primary_sweet_spot"],
                "tier_badge": meta_c["tier_badge"],
                "expected_accuracy_pct": kpis_c["expected_accuracy_pct"],
                "expected_mae_mw": kpis_c["expected_mae_mw"],
                "mean_ml_predicted_mw": kpis_c["mean_ml_predicted_mw"],
                "mean_p10_safe_floor_mw": kpis_c["mean_p10_safe_floor_mw"],
                "realized_ml_mae_mw": kpis_c.get("realized_ml_mae_mw"),
                "realized_latest_mae_mw": kpis_c.get("realized_latest_mae_mw"),
                "realized_ml_accuracy_pct": kpis_c.get("realized_ml_accuracy_pct"),
                "realized_latest_accuracy_pct": kpis_c.get("realized_latest_accuracy_pct"),
                "mean_mw_error_saved_per_hour": kpis_c.get("mean_mw_error_saved_per_hour"),
            }
        )

    cutoff_meta = get_cutoff_metadata(cutoff_time)
    model_meta = MODEL_PREDICTOR.get_model_metadata()

    return {
        "request_context": {
            "delivery_date": delivery_date,
            "cutoff_time_utc": cutoff_time,
            "cutoff_publish_timestamp_utc": cutoff_publish_ts,
            "matrix_shape": [len(X_day_24x9), len(X_day_24x9[0]) if X_day_24x9 else 0],
            "cutoff_recommendation": cutoff_meta,
            "model_metadata": model_meta,
        },
        "daily_kpis": evaluated["daily_kpis"],
        "hourly_slots": evaluated["hourly_slots"],
        "day_cutoff_comparison": day_cutoff_comparison,
    }


try:
    from fastapi import APIRouter, Depends, HTTPException, Query
    from backend.db.database import get_db
    from backend.schemas import DayAheadForecastResponseSchema

    router = APIRouter(prefix="/api/v1/forecast", tags=["Day-Ahead 24h Forecast"])

    @router.get(
        "/day-ahead",
        response_model=DayAheadForecastResponseSchema,
        summary="Execute 24-hour causal feature extraction, vectorized .pkl inference, and risk estimation",
    )
    def get_day_ahead_forecast(
        delivery_date: str = Query("2026-01-15", description="Target delivery date (YYYY-MM-DD)"),
        cutoff_time: str = Query("15:30", description="Day-1 decision cutoff time UTC (02:30 .. 22:30)"),
        auto_ingest_if_missing: bool = Query(True, description="Fetch from live Elexon BMRS if date is not in DB"),
        db: DatabaseConnection = Depends(get_db),
    ) -> Dict[str, Any]:
        try:
            return build_day_ahead_forecast_payload(
                db=db,
                delivery_date=delivery_date,
                cutoff_time=cutoff_time,
                auto_ingest_if_missing=auto_ingest_if_missing,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

except ImportError:
    router = None
