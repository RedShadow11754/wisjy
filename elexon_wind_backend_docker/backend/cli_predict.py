"""
Production CLI & Diagnostic Report Utility (backend/cli_predict.py).

Executes the end-to-end Day-Ahead Wind Procurement pipeline from the command line:
  1. Queries `raw_windfor_forecasts` in PostgreSQL up to the requested Day-1 cutoff (`publish_time <= cutoff`).
  2. Extracts the `(24, 9)` trajectory feature matrix across all 24 settlement hours (`00:00`..`23:00 UTC`).
  3. Runs vectorized `.pkl` ensemble inference (`wind_procurement_model.pkl`) in one batch call.
  4. Computes ex-ante P10/P50/P90 MW bounds, expected accuracy %, and realized settlement audits.

Usage:
  docker compose exec api python3 -m backend.cli_predict --date 2026-01-15 --cutoff 07:30
  docker compose exec api python3 -m backend.cli_predict --date 2026-01-15 --compare-cutoffs
"""

import argparse
from backend.config import VALID_CUTOFFS_UTC
from backend.db.database import DatabaseConnection
from backend.services.feature_extractor import extract_24h_features_for_cutoff
from backend.services.model_predictor import MODEL_PREDICTOR
from backend.services.accuracy_estimator import estimate_24h_accuracy_and_intervals
from backend.services.cutoff_advisor import get_cutoff_metadata


def run_single_cutoff_report(delivery_date: str, cutoff_time: str) -> None:
    db = DatabaseConnection()
    try:
        X_day_24x9, hourly_records, cutoff_publish_ts = extract_24h_features_for_cutoff(
            db=db,
            delivery_date=delivery_date,
            cutoff_time_utc=cutoff_time,
        )
        ml_pred_24, resid_adj_24, _, _ = MODEL_PREDICTOR.predict_24h_vectorized(X_day_24x9)

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

        evaluated = estimate_24h_accuracy_and_intervals(
            hourly_records=hourly_records,
            ml_predicted_mw_24=ml_pred_24,
            predicted_residual_mw_24=resid_adj_24,
            actuals_by_hour=actuals_by_hour,
        )
    finally:
        db.close()

    kpis = evaluated["daily_kpis"]
    slots = evaluated["hourly_slots"]
    c_meta = get_cutoff_metadata(cutoff_time)
    m_meta = MODEL_PREDICTOR.get_model_metadata()

    print("=" * 114)
    print(f" UK DAY-AHEAD WIND PROCUREMENT ENGINE | DELIVERY DATE: {delivery_date} | CUTOFF: {cutoff_time} UTC (Day - 1)")
    print("=" * 114)
    print(f" Cutoff Publish Timestamp : {cutoff_publish_ts}  [{c_meta['tier_badge']}]")
    print(f" Model Artifact           : {m_meta['model_version']}  ({m_meta['execution_engine']})")
    print(f" Training Window          : {m_meta['training_window']}")
    print(f" Feature Matrix Shape     : ({len(X_day_24x9)}, {len(X_day_24x9[0])}) -> 24 target hours x 9 live trajectory features")
    print("-" * 114)
    print(" EX-ANTE PRE-DELIVERY PROCUREMENT GUIDANCE (Known at Cutoff Time BEFORE Delivery Day Starts):")
    print(f"   • Expected Daily Wind Volume (P50) : {kpis['total_daily_ml_mwh']:,.0f} MWh  (Avg: {kpis['mean_ml_predicted_mw']:,.1f} MW/h)")
    print(f"   • Expected Accuracy Score          : {kpis['expected_accuracy_pct']:.1f}%")
    print(f"   • Expected Hourly Error (±MAE)     : ±{kpis['expected_mae_mw']:,.1f} MW")
    print(f"   • Firm P10 Safe Floor (90% Conf.)  : {kpis['total_daily_safe_firm_mwh']:,.0f} MWh  (Avg: {kpis['mean_p10_safe_floor_mw']:,.1f} MW/h)")
    print("-" * 114)

    if "realized_ml_mae_mw" in kpis:
        print(" POST-DELIVERY SETTLEMENT AUDIT (Realized vs Elexon FUELHH Actuals):")
        print(f"   • Realized ML MAE                  : {kpis['realized_ml_mae_mw']:,.1f} MW  (Realized Accuracy: {kpis['realized_ml_accuracy_pct']:.1f}%)")
        print(f"   • Raw Elexon Latest MAE            : {kpis['realized_latest_mae_mw']:,.1f} MW  (Realized Accuracy: {kpis['realized_latest_accuracy_pct']:.1f}%)")
        print(f"   • Mean Error Saved per Hour by ML  : {kpis['mean_mw_error_saved_per_hour']:+,.1f} MW/h  ({kpis['realized_mae_reduction_pct']:+.1f}% error reduction)")
        print(f"   • Empirical P10 Safe Floor Hit Rate: {kpis['empirical_p10_safe_floor_hit_rate_pct']:.1f}% of hours >= P10 Floor")
        print("-" * 114)

    print(
        f"{'Hour UTC':<10} | {'Revs':>4} | {'First MW':>9} | {'Latest MW':>9} | {'EWMA MW':>9} | "
        f"{'P10 Floor':>9} | {'ML P50 MW':>9} | {'P90 Cap':>9} | {'Exp Acc%':>8} | {'Actual MW':>9} | {'ML Saved':>9}"
    )
    print("-" * 114)
    for s in slots:
        feats = s["features"]
        hr_str = f"{s['target_hour_utc']} UTC"
        revs = int(feats["prediction_count"])
        first_mw = feats["first_forecast"]
        latest_mw = s["latest_forecast_mw"]
        ewma_mw = feats["ewma_forecast"]
        p10_mw = s["p10_safe_floor_mw"]
        ml_mw = s["p50_ml_forecast_mw"]
        p90_mw = s["p90_upper_bound_mw"]
        exp_acc = s["expected_accuracy_pct"]
        act_str = f"{s['actual_wind_mw']:9.1f}" if "actual_wind_mw" in s else "      N/A"
        sav_str = f"{s['mw_error_saved_by_ml']:+9.1f}" if "mw_error_saved_by_ml" in s else "      N/A"
        print(
            f"{hr_str:<10} | {revs:>4d} | {first_mw:>9.1f} | {latest_mw:>9.1f} | {ewma_mw:>9.1f} | "
            f"{p10_mw:>9.1f} | {ml_mw:>9.1f} | {p90_mw:>9.1f} | {exp_acc:>7.1f}% | {act_str} | {sav_str}"
        )
    print("=" * 114)


def run_cutoff_comparison_report(delivery_date: str) -> None:
    db = DatabaseConnection()
    try:
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

        rows = []
        for c_utc in VALID_CUTOFFS_UTC:
            X_c, recs_c, _ = extract_24h_features_for_cutoff(db, delivery_date, c_utc)
            pred_c, res_c, _, _ = MODEL_PREDICTOR.predict_24h_vectorized(X_c)
            eval_c = estimate_24h_accuracy_and_intervals(recs_c, pred_c, res_c, actuals_by_hour)
            kpis_c = eval_c["daily_kpis"]
            meta_c = get_cutoff_metadata(c_utc)
            rows.append((c_utc, meta_c, kpis_c))
    finally:
        db.close()

    print("=" * 118)
    print(f" 8-CUTOFF DECISION ADVISOR | DELIVERY DATE: {delivery_date} | ALL DAY-1 ELEXON CUTOFF WINDOWS")
    print("=" * 118)
    print(
        f"{'Cutoff':<8} | {'Rec':<4} | {'Tranche':>7} | {'Exp Acc%':>8} | {'Exp ±MAE':>9} | "
        f"{'P50 Volume':>11} | {'P10 Floor':>11} | {'Real ML MAE':>11} | {'Raw MAE':>9} | {'Saved/hr':>9}"
    )
    print("-" * 118)
    for c_utc, meta_c, kpis_c in rows:
        star = "★" if meta_c.get("is_primary_sweet_spot") else ("✓" if meta_c.get("is_recommended") else " ")
        tranche_pct = meta_c.get("tranche_allocation_pct", 0)
        real_ml = f"{kpis_c['realized_ml_mae_mw']:8.1f} MW" if "realized_ml_mae_mw" in kpis_c else "        N/A"
        real_raw = f"{kpis_c['realized_latest_mae_mw']:6.1f} MW" if "realized_latest_mae_mw" in kpis_c else "      N/A"
        sav_hr = f"{kpis_c['mean_mw_error_saved_per_hour']:+6.1f} MW" if "mean_mw_error_saved_per_hour" in kpis_c else "      N/A"
        print(
            f"{c_utc:<8} |  {star}   | {tranche_pct:>6d}% | {kpis_c['expected_accuracy_pct']:>7.1f}% | "
            f"±{kpis_c['expected_mae_mw']:>5.1f} MW | {kpis_c['total_daily_ml_mwh']:>7,.0f} MWh | "
            f"{kpis_c['total_daily_safe_firm_mwh']:>7,.0f} MWh | {real_ml:>11} | {real_raw:>9} | {sav_hr:>9}"
        )
    print("=" * 118)


def main():
    parser = argparse.ArgumentParser(description="UK Day-Ahead Wind Procurement Engine CLI")
    parser.add_argument("--date", type=str, default="2026-01-15", help="Delivery date in YYYY-MM-DD format (e.g. 2026-01-15)")
    parser.add_argument("--cutoff", type=str, default="07:30", help="Day-1 cutoff time in HH:MM format (e.g. 07:30, 15:30)")
    parser.add_argument("--compare-cutoffs", action="store_true", help="Compare all 8 daily cutoffs for the given delivery date")
    args = parser.parse_args()

    if args.compare_cutoffs:
        run_cutoff_comparison_report(args.date)
    else:
        run_single_cutoff_report(args.date, args.cutoff)


if __name__ == "__main__":
    main()
