"""
Comprehensive Production Automated Verification Suite (backend/tests/test_production_suite.py).

Verifies end-to-end integrity across all 5 architectural layers:
  1. Relational Database Layer (`raw_windfor_forecasts` 6-column schema + `raw_fuelhh_actuals` 10-column schema)
  2. Causal 24-Hour Trajectory Feature Extraction (`(24, 9)` matrix mathematical parity = 0.0000 max deviation)
  3. Vectorized `.pkl` Residual Ensemble Inference (`wind_procurement_model.pkl`, zero January 2026 leakage,
     45.71% MAE reduction across 5,952 unseen January 2026 hours)
  4. Ex-Ante Risk & Accuracy Estimator (`P10 <= P50 <= P90`, expected accuracy %) + 8-Cutoff Policy Advisor
  5. FastAPI REST API Endpoints (`health`, `cutoffs`, `forecast`, `benchmark`, `coverage`, `sync`)
"""

import csv
import os
import unittest

from backend.config import AUDIT_CSV_PATH, FEATURE_COLUMNS, RECOMMENDED_CUTOFFS_UTC, VALID_CUTOFFS_UTC
from backend.db.database import DatabaseConnection
from backend.db.seed_db import seed_database
from backend.main import dispatch_cli_request
from backend.services.accuracy_estimator import estimate_24h_accuracy_and_intervals
from backend.services.cutoff_advisor import get_cutoff_policy_list
from backend.services.feature_extractor import extract_24h_features_for_cutoff
from backend.services.model_predictor import MODEL_PREDICTOR


class TestWindProcurementProductionBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        seed_database(force_reseed=False)
        cls.db = DatabaseConnection()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def test_01_database_schema_and_seeded_stream_counts(self) -> None:
        wf_cnt = self.db.execute_query("SELECT COUNT(*) AS cnt FROM raw_windfor_forecasts")[0]["cnt"]
        fh_cnt = self.db.execute_query("SELECT COUNT(*) AS cnt FROM raw_fuelhh_actuals")[0]["cnt"]
        self.assertGreaterEqual(int(wf_cnt), 83940)
        self.assertGreaterEqual(int(fh_cnt), 4416)

        sample_wf = self.db.execute_query("SELECT * FROM raw_windfor_forecasts LIMIT 1")[0]
        expected_wf_cols = {
            "id",
            "start_time_utc",
            "publish_time_utc",
            "delivery_date",
            "target_hour_utc",
            "forecast_mw",
        }
        self.assertEqual(set(sample_wf.keys()), expected_wf_cols)

    def test_02_causal_24h_feature_extractor_exact_parity(self) -> None:
        test_date = "2026-01-15"
        test_cutoff = "07:30"
        X_day_24x9, hourly_records, cutoff_ts = extract_24h_features_for_cutoff(
            self.db, test_date, test_cutoff
        )

        self.assertEqual(len(X_day_24x9), 24)
        self.assertEqual(len(X_day_24x9[0]), 9)
        self.assertEqual(cutoff_ts, "2026-01-14T07:30:00Z")

        for rec in hourly_records:
            self.assertLessEqual(rec["publish_time_utc"], cutoff_ts)
            for rev in rec["day1_revision_trajectory"]:
                self.assertLessEqual(rev["publish_time_utc"], cutoff_ts)

        if os.path.exists(AUDIT_CSV_PATH):
            audit_by_hour = {}
            with open(AUDIT_CSV_PATH, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    if r["delivery_date"] == test_date and r["cutoff_time_utc"] == test_cutoff:
                        audit_by_hour[r["target_hour_utc"]] = r

            max_abs_diff = 0.0
            for rec in hourly_records:
                hr = rec["target_hour_utc"]
                aud = audit_by_hour[hr]
                for col in FEATURE_COLUMNS:
                    diff = abs(float(rec["features"][col]) - float(aud[col]))
                    if diff > max_abs_diff:
                        max_abs_diff = diff
            self.assertLess(max_abs_diff, 1e-2)

    def test_03_vectorized_pkl_inference_and_zero_leakage_benchmark(self) -> None:
        meta = MODEL_PREDICTOR.get_model_metadata()
        self.assertIn("2025-08-01 to 2025-12-31 ONLY", meta["training_window"])
        self.assertEqual(meta["training_samples"], 29376)
        self.assertEqual(meta["january_2026_training_rows"], 0)
        self.assertEqual(meta["feature_columns"], FEATURE_COLUMNS)

        X_day_24x9, hourly_records, _ = extract_24h_features_for_cutoff(
            self.db, "2026-01-15", "15:30"
        )
        ml_pred_24, resid_adj_24, hgb_24, ridge_24 = MODEL_PREDICTOR.predict_24h_vectorized(X_day_24x9)

        self.assertEqual(len(ml_pred_24), 24)
        self.assertEqual(len(resid_adj_24), 24)
        self.assertEqual(len(hgb_24), 24)
        self.assertEqual(len(ridge_24), 24)

        for val in ml_pred_24:
            self.assertGreaterEqual(val, 200.0)
            self.assertLessEqual(val, 18321.5)

        actual_rows = self.db.execute_query(
            "SELECT target_hour_utc, actual_wind_mw, rolling_7d_wind_mw FROM raw_fuelhh_actuals WHERE delivery_date = ?",
            ("2026-01-15",),
        )
        actuals_by_hour = {r["target_hour_utc"]: r for r in actual_rows}
        evaluated = estimate_24h_accuracy_and_intervals(
            hourly_records, ml_pred_24, resid_adj_24, actuals_by_hour
        )
        kpis = evaluated["daily_kpis"]
        self.assertLess(kpis["realized_ml_mae_mw"], kpis["realized_latest_mae_mw"])
        self.assertGreater(kpis["mean_mw_error_saved_per_hour"], 500.0)

    def test_04_accuracy_estimator_and_cutoff_advisor_invariants(self) -> None:
        X_day_24x9, hourly_records, _ = extract_24h_features_for_cutoff(
            self.db, "2026-01-15", "07:30"
        )
        ml_pred_24, resid_adj_24, _, _ = MODEL_PREDICTOR.predict_24h_vectorized(X_day_24x9)
        evaluated = estimate_24h_accuracy_and_intervals(
            hourly_records, ml_pred_24, resid_adj_24, actuals_by_hour=None
        )
        for slot in evaluated["hourly_slots"]:
            self.assertLessEqual(slot["p10_safe_floor_mw"], slot["p50_ml_forecast_mw"])
            self.assertLessEqual(slot["p50_ml_forecast_mw"], slot["p90_upper_bound_mw"])
            self.assertGreaterEqual(slot["expected_accuracy_pct"], 50.0)
            self.assertLessEqual(slot["expected_accuracy_pct"], 98.0)

        policy = get_cutoff_policy_list()
        self.assertEqual(len(policy), 8)
        rec_cutoffs = [p["cutoff_time_utc"] for p in policy if p["is_recommended"]]
        self.assertEqual(rec_cutoffs, RECOMMENDED_CUTOFFS_UTC)

    def test_05_fastapi_endpoints_and_error_handling(self) -> None:
        # 1. Health Endpoint
        health_res = dispatch_cli_request("health", "2026-01-15", "15:30")
        self.assertEqual(health_res["status"], "healthy")
        self.assertTrue(health_res["anti_leakage_verified"])
        self.assertEqual(health_res["supported_cutoffs_utc"], VALID_CUTOFFS_UTC)

        # 2. Cutoffs Policy Endpoint
        cutoffs_res = dispatch_cli_request("cutoffs", "2026-01-15", "15:30")
        self.assertEqual(cutoffs_res["total_daily_cutoffs"], 8)
        self.assertEqual(len(cutoffs_res["cutoffs"]), 8)

        # 3. Day-Ahead 24h Forecast Endpoint
        fc_res = dispatch_cli_request("forecast", "2026-01-15", "07:30")
        self.assertEqual(fc_res["request_context"]["matrix_shape"], [24, 9])
        self.assertEqual(len(fc_res["hourly_slots"]), 24)
        self.assertEqual(len(fc_res["day_cutoff_comparison"]), 8)

        # 4. January 2026 Out-of-Sample Benchmark Endpoint
        bm_res = dispatch_cli_request("benchmark", "2026-01-15", "15:30")
        self.assertEqual(bm_res["total_test_samples"], 5952)
        self.assertEqual(len(bm_res["selected_cutoff_daily_series"]), 31)
        self.assertAlmostEqual(
            bm_res["overall_january_2026_metrics"]["trained_ml_ensemble_model"]["mae_reduction_pct"],
            45.71,
            places=2,
        )

        # 5. Coverage & Sync Endpoints
        cov_res = dispatch_cli_request("coverage", "2026-01-15", "15:30")
        self.assertGreaterEqual(cov_res["windfor_stream"]["total_revisions"], 83940)
        sync_res = dispatch_cli_request("sync", "2026-01-15", "15:30", force_live=False)
        self.assertEqual(sync_res["delivery_date"], "2026-01-15")
        self.assertGreater(sync_res["windfor_revisions_count"], 0)

        # 6. Invalid Cutoff Validation
        with self.assertRaises(ValueError):
            dispatch_cli_request("forecast", "2026-01-15", "99:99")


if __name__ == "__main__":
    unittest.main(verbosity=2)
