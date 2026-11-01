# UK Day-Ahead Wind Procurement Engine — React Frontend Engineering Handoff Guide (`frontend.md`)

---

## 1. Executive Summary & What This Project Is About

### 1.1 The Core Business Problem
In the Great Britain (GB) wholesale electricity market, **Day-Ahead Energy Procurement & Trading Desks** must decide on **Day-1** (the day before delivery) how many megawatts (MW) of wind power to lock in for each of the **24 settlement hours (`00:00` to `23:00 UTC`) of Day-0** (the target delivery date).

National Grid ESO / Elexon Balancing Mechanism Reporting Service (**BMRS**) publishes rolling wind generation forecasts (`WINDFOR`) throughout Day-2 and Day-1. However, **relying directly on raw Elexon `WINDFOR` forecasts exposes a trading desk to severe financial imbalance penalties**:
- Across all 31 days and 8 daily decision cutoffs of **January 2026 (`5,952` Cutoff × Hour evaluations)**, the untrained raw Elexon `latest_forecast` exhibits a Mean Absolute Error (**MAE**) of **`2,064.52 MW`** (`RMSE: 2,542.96 MW`, `R²: 0.6249`) and a systematic **over-prediction bias of `+1,810.69 MW`**.
- If a procurement desk locks in raw Elexon forecasts, it routinely over-commits wind volume that fails to materialize at delivery, forcing the desk to buy expensive balancing power in the intraday/imbalance market.

### 1.2 How Our Backend Engine Solves It
The backend (`FastAPI + PostgreSQL 16 + Scikit-Learn Vectorized Residual Ensemble`) sits between raw Elexon BMRS streams (`WINDFOR` forecasts and `FUELHH` half-hourly metered wind actuals) and your React frontend application.

For any requested **`delivery_date`** (`YYYY-MM-DD`) and **Day-1 `cutoff_time`** (`02:30`, `04:30`, `07:30`, `09:30`, `11:30`, `15:30`, `18:30`, or `22:30 UTC`), the backend executes a deterministic 4-stage pipeline in **`~12–35 ms`**:

1. **Strict Causal PostgreSQL Query (`raw_windfor_forecasts`)**:
   - Retrieves all Elexon `WINDFOR` forecast revisions targeting the 24 hours of `delivery_date` that were published **strictly at or before** `Day-1 cutoff_time` (`publish_time_utc <= cutoff_publish_timestamp_utc`).
   - **Zero Data Leakage**: Revisions published after the selected Day-1 cutoff are never visible to the query. Furthermore, the production model (`wind_procurement_model.pkl`) was trained exclusively on **August–December 2025 (`29,376` rows)** with **zero January 2026 exposure**.
2. **Trajectory Feature Extraction (`(24, 9)` Matrix)**:
   - Condenses the revision history of each target hour (`00:00` to `23:00 UTC`) into **9 physical and revision-trajectory features** (`latest_forecast`, `first_forecast`, `ewma_forecast`, `mean_forecast`, `std_forecast`, `last_revision_delta`, `overall_trend_slope`, `prediction_count`, `lead_time_hours`).
3. **Vectorized `.pkl` Residual Inference**:
   - Feeds the `(24, 9)` matrix into `wind_procurement_model.pkl` (a 65% HistGradientBoosting + 35% Ridge residual-learning ensemble) in a single vectorized call.
   - The model predicts the systematic Elexon residual error (`ml_residual_adjustment_mw`) for each hour and produces the bias-corrected central forecast (`p50_ml_forecast_mw = ml_predicted_mw`), cutting January 2026 out-of-sample MAE by **`45.71%`** (down to **`1,120.78 MW`**, saving **`943.74 MW` of error per hour**, raising `R²` from `0.6249` to **`0.8792`**).
4. **Ex-Ante Risk Bands & Post-Settlement Audit**:
   - Attaches **probabilistic procurement bands (`p10_safe_floor_mw`, `p50_ml_forecast_mw`, `p90_upper_bound_mw`)** and **ex-ante expected accuracy (`expected_accuracy_pct`, `expected_error_mw`)** calibrated strictly on the December 2025 validation holdout set.
   - When post-delivery Elexon `FUELHH` metered actuals exist for the selected date, the backend automatically attaches **realized settlement audit metrics** (`actual_wind_mw`, `realized_ml_error_mw`, `realized_latest_error_mw`, `mw_error_saved_by_ml`) so traders can backtest and audit historical performance.

---

## 2. Core Domain Concepts the Frontend Must Understand

Before building React components, make sure the team understands these four domain distinctions returned by the API:

### 2.1 Delivery Date (`Day-0`) vs. Decision Cutoff (`Day-1`)
- **`delivery_date` (`YYYY-MM-DD`)**: The UTC calendar day when wind electricity is physically generated and settled on the UK grid (`00:00` through `23:00 UTC`, 24 1-hour slots).
- **`cutoff_time` (`HH:MM` UTC)**: The timestamp on **the previous calendar day (`Day-1`)** when the trader freezes the forecast book to place Day-Ahead market orders.
  - *Example*: If `delivery_date = "2026-01-15"` and `cutoff_time = "07:30"`, the backend freezes knowledge at **`2026-01-14T07:30:00Z`** (`cutoff_publish_timestamp_utc`).

### 2.2 The 8 Supported Day-1 Cutoffs & Recommended 3-Tranche Strategy
Elexon updates `WINDFOR` batches throughout Day-1. The backend supports **8 standard Day-1 cutoff windows**:
- `02:30`, `04:30`, `07:30`, `09:30`, `11:30`, `15:30`, `18:30`, `22:30` (all UTC).
- **Recommended Execution Cutoffs**: `["07:30", "11:30", "15:30", "18:30"]`
  - **Tranche 1 (`07:30 UTC` — 30% volume)**: Morning desk open; captures the first major meteorological NWP revision drop (`-118 MW` validation error reduction vs `02:30`).
  - **Tranche 2 (`11:30 UTC` — 35% volume)**: Pre-auction lock prior to the 11:00–12:00 UTC EPEX/NordPool Day-Ahead coupled auction clearing.
  - **Tranche 3 (`15:30` or `18:30 UTC` — 35% volume)**: **Primary Accuracy Sweet Spot** (`>50.7%` error reduction vs raw Elexon; `~87.9%` realized accuracy). Waiting until `22:30 UTC` is discouraged (` Late Lock` — liquidity dries up and accuracy slightly degrades).

### 2.3 P10 / P50 / P90 Megawatt Numbers (What Traders Actually Buy)
For every hour (`00:00` to `23:00 UTC`), the API provides four megawatt curves:
1. **`latest_forecast_mw`**: The raw, uncorrected Elexon `WINDFOR` forecast published at or before the cutoff. Show this as a reference baseline so the user sees how much over-prediction bias the ML model removed.
2. **`p50_ml_forecast_mw` (`ml_predicted_mw`)**: Our ML ensemble's central bias-corrected prediction (`latest_forecast_mw + ml_residual_adjustment_mw`).
3. **`p10_safe_floor_mw`**: The **90%-confidence lower bound** (`P10 Safe Firm Capacity Floor`). Traders can commit this megawatt volume as firm baseload wind with minimal risk of under-generation penalties.
4. **`p90_upper_bound_mw`**: The **90%-confidence upper ceiling** (`P90 Upside Cap`), representing peak generation potential if wind speeds exceed central expectations.

### 2.4 Ex-Ante (Pre-Delivery) vs. Ex-Post (Realized Settlement) Fields
- **Ex-Ante Fields (Always Present)**: `expected_error_mw`, `expected_accuracy_pct`, `confidence_tier`, `p10_safe_floor_mw`, `p50_ml_forecast_mw`, `p90_upper_bound_mw`. These are computed **strictly before delivery** using the 9 trajectory features and December 2025 validation calibration. They work on live future dates where actuals do not exist yet.
- **Ex-Post Realized Fields (Nullable / Optional)**: `actual_wind_mw`, `realized_ml_error_mw`, `realized_latest_error_mw`, `realized_ml_accuracy_pct`, `realized_latest_accuracy_pct`, `mw_error_saved_by_ml`. These are populated when `raw_fuelhh_actuals` has settled data for `delivery_date` (such as all historical dates from `2025-08-01` through `2026-01-31` and any settled live BMRS date). Your React UI should gracefully check `slot.actual_wind_mw !== null && slot.actual_wind_mw !== undefined` to toggle settlement overlays on/off.

---

## 3. Connection, Base URL, Headers & Error Format

### 3.1 Base URL Configuration
In your React environment (`.env` / Vite config):
- **Local Docker Desktop Stack**: `VITE_API_BASE_URL=http://localhost:8000`
- **Same-Origin Reverse Proxy / Dev Server**: `VITE_API_BASE_URL=""` (relative `/api/v1/...`)
- **Interactive Swagger UI (OpenAPI 3.1)**: `http://localhost:8000/docs`
- **OpenAPI JSON Spec**: `http://localhost:8000/openapi.json`

### 3.2 CORS & Custom Response Headers
- **CORS**: Enabled for all origins (`Access-Control-Allow-Origin: *`), methods (`GET`, `POST`, `OPTIONS`), and headers.
- **`X-Process-Time-Ms` Header**: Every HTTP response from the backend includes `X-Process-Time-Ms` (e.g., `"14.82"`), measuring exact server-side SQL + ML inference execution time in milliseconds.

### 3.3 Standard Error Payload (`HTTP 400 / 422 / 500`)
When an invalid `delivery_date` format or unsupported `cutoff_time` is passed, FastAPI returns `HTTP 400 Bad Request`:
```json
{
  "detail": "Invalid cutoff_time_utc '08:00'. Must be one of ('02:30', '04:30', '07:30', '09:30', '11:30', '15:30', '18:30', '22:30')."
}
```
*(Note: If bridged through an Express proxy, check `err.detail || err.error` when displaying error banners in React).*

---

## 4. Complete TypeScript Interfaces (`src/types/windApi.ts`)

Copy this file directly into your React codebase (`src/types/windApi.ts`). It matches the backend Pydantic v2 schemas (`backend/schemas.py`) 1-for-1:

```typescript
// src/types/windApi.ts

export type CutoffTimeUtc =
  | '02:30'
  | '04:30'
  | '07:30'
  | '09:30'
  | '11:30'
  | '15:30'
  | '18:30'
  | '22:30';

export const VALID_CUTOFFS_UTC: CutoffTimeUtc[] = [
  '02:30',
  '04:30',
  '07:30',
  '09:30',
  '11:30',
  '15:30',
  '18:30',
  '22:30',
];

export const RECOMMENDED_CUTOFFS_UTC: CutoffTimeUtc[] = [
  '07:30',
  '11:30',
  '15:30',
  '18:30',
];

// ============================================================================
// 1. GET /api/v1/system/health
// ============================================================================
export interface DatabaseStatus {
  connected: boolean;
  engine: 'postgresql' | 'sqlite' | string;
  raw_windfor_forecasts_rows: number;
  raw_fuelhh_actuals_rows: number;
  unseen_january_2026_days: number;
}

export interface MlModelMetadata {
  artifact_filename: string;
  artifact_sha256: string;
  model_architecture: string;
  target_variable: string;
  inference_formula: string;
  training_window: string;
  validation_window: string;
  test_window_unseen: string;
  feature_count: number;
  feature_columns: string[];
  ensemble_weights: {
    hist_gradient_boosting: number;
    ridge_regression: number;
  };
  jan2026_unseen_test_metrics: {
    latest_forecast_mae_mw: number;
    ml_model_mae_mw: number;
    mae_reduction_pct: number;
    r2_score: number;
  };
}

export interface SystemHealthResponse {
  status: 'healthy' | string;
  service_name: string;
  backend_framework: string;
  database: DatabaseStatus;
  ml_model: MlModelMetadata;
  anti_leakage_verified: boolean;
  supported_cutoffs_utc: CutoffTimeUtc[];
  recommended_cutoffs_utc: CutoffTimeUtc[];
}

// ============================================================================
// 2. GET /api/v1/cutoffs/policy
// ============================================================================
export interface CutoffPolicyItem {
  cutoff_time_utc: CutoffTimeUtc;
  avg_lead_time_hours: number;
  dec2025_val_mae_mw: number;
  jan2026_untrained_latest_mae_mw: number;
  jan2026_ml_mae_mw: number;
  jan2026_mae_reduction_pct: number;
  expected_accuracy_pct: number;
  is_recommended: boolean;
  is_primary_sweet_spot: boolean;
  tranche_allocation_pct: number;
  tier_badge: string;
  trading_rationale: string;
}

export interface CutoffPolicyResponse {
  total_daily_cutoffs: number;
  recommended_cutoffs_utc: CutoffTimeUtc[];
  primary_sweet_spot_cutoffs_utc: CutoffTimeUtc[];
  calibration_source: string;
  recommended_execution_strategy: {
    tranche_1: string;
    tranche_2: string;
    tranche_3_sweet_spot: string;
  };
  cutoffs: CutoffPolicyItem[];
}

// ============================================================================
// 3. GET /api/v1/forecast/day-ahead
// ============================================================================
export interface NineTrajectoryFeatures {
  latest_forecast: number;
  first_forecast: number;
  ewma_forecast: number;
  mean_forecast: number;
  std_forecast: number;
  last_revision_delta: number;
  overall_trend_slope: number;
  prediction_count: number;
  lead_time_hours: number;
}

export interface RevisionTrajectoryPoint {
  publish_time_utc: string;
  forecast_mw: number;
  lead_time_hours: number;
}

export interface HourlyForecastSlot {
  target_hour_utc: string;             // "00:00" .. "23:00"
  start_time_utc: string;              // e.g. "2026-01-15T00:00:00Z"
  publish_time_utc: string;            // Last revision timestamp <= cutoff
  cutoff_time_utc: CutoffTimeUtc;
  lead_time_hours: number;
  latest_forecast_mw: number;          // Raw Elexon WINDFOR baseline
  ml_predicted_mw: number;             // Same as p50_ml_forecast_mw
  ml_residual_adjustment_mw: number;   // Signed MW correction applied by ML
  p10_safe_floor_mw: number;           // 90% confidence safe firm commitment floor
  p50_ml_forecast_mw: number;          // Central ML forecast
  p90_upper_bound_mw: number;          // 90% confidence upper generation bound
  expected_error_mw: number;           // Ex-ante expected MAE (MW)
  expected_accuracy_pct: number;       // Ex-ante expected accuracy (%)
  confidence_tier:
    | 'Very High Confidence (Tight Lock)'
    | 'High Confidence (Standard Lock)'
    | 'Moderate Confidence (Wide Hedge)'
    | string;
  features: NineTrajectoryFeatures;
  day1_revision_trajectory: RevisionTrajectoryPoint[];

  // Post-delivery settlement audit fields (present when FUELHH actuals exist)
  actual_wind_mw?: number | null;
  rolling_7d_wind_mw?: number | null;
  realized_ml_error_mw?: number | null;
  realized_latest_error_mw?: number | null;
  realized_ml_accuracy_pct?: number | null;
  realized_latest_accuracy_pct?: number | null;
  mw_error_saved_by_ml?: number | null;
}

export interface DailyForecastKpis {
  hours_count: number;                 // Always 24
  mean_latest_forecast_mw: number;
  mean_ml_predicted_mw: number;
  mean_p10_safe_floor_mw: number;
  mean_p90_upper_bound_mw: number;
  total_daily_ml_mwh: number;          // Sum of 24h P50 predictions (MWh)
  total_daily_safe_firm_mwh: number;   // Sum of 24h P10 safe floor (MWh)
  expected_mae_mw: number;
  expected_accuracy_pct: number;

  // Present when FUELHH actuals exist for delivery_date
  mean_actual_wind_mw?: number;
  total_daily_actual_mwh?: number;
  realized_ml_mae_mw?: number;
  realized_latest_mae_mw?: number;
  realized_ml_rmse_mw?: number;
  realized_latest_rmse_mw?: number;
  realized_ml_accuracy_pct?: number;
  realized_latest_accuracy_pct?: number;
  mean_mw_error_saved_per_hour?: number;
  realized_mae_reduction_pct?: number;
  empirical_p10_safe_floor_hit_rate_pct?: number;
  empirical_p10_p90_band_coverage_pct?: number;
}

export interface DayCutoffComparisonItem {
  cutoff_time_utc: CutoffTimeUtc;
  is_selected: boolean;
  is_recommended: boolean;
  is_primary_sweet_spot: boolean;
  tier_badge: string;
  expected_accuracy_pct: number;
  expected_mae_mw: number;
  mean_ml_predicted_mw: number;
  mean_p10_safe_floor_mw: number;
  realized_ml_mae_mw?: number | null;
  realized_latest_mae_mw?: number | null;
  realized_ml_accuracy_pct?: number | null;
  realized_latest_accuracy_pct?: number | null;
  mean_mw_error_saved_per_hour?: number | null;
}

export interface DayAheadForecastResponse {
  request_context: {
    delivery_date: string;
    cutoff_time_utc: CutoffTimeUtc;
    cutoff_publish_timestamp_utc: string;
    matrix_shape: [number, number];    // Always [24, 9]
    cutoff_recommendation: CutoffPolicyItem;
    model_metadata: MlModelMetadata;
  };
  daily_kpis: DailyForecastKpis;
  hourly_slots: HourlyForecastSlot[];  // Always 24 items ("00:00" .. "23:00")
  day_cutoff_comparison: DayCutoffComparisonItem[]; // Always 8 items ("02:30" .. "22:30")
}

// ============================================================================
// 4. GET /api/v1/benchmark/january-overview
// ============================================================================
export interface JanuaryDailyBenchmarkPoint {
  delivery_date: string;               // "2026-01-01" .. "2026-01-31"
  cutoff_time_utc: CutoffTimeUtc;
  mean_actual_mw: number;
  mean_latest_forecast_mw: number;
  mean_ml_predicted_mw: number;
  rolling_7d_wind_mw: number;
  untrained_latest_mae_mw: number;
  ml_model_mae_mw: number;
  mw_error_saved_per_hour: number;
  untrained_latest_accuracy_pct: number;
  ml_model_accuracy_pct: number;
}

export interface JanuaryBenchmarkResponse {
  evaluation_month: string;
  training_window_used: string;
  total_test_samples: number;          // 5952 (31 days * 8 cutoffs * 24 hours)
  overall_january_2026_metrics: {
    untrained_latest_forecast: {
      mae_mw: number;
      rmse_mw: number;
      mse_mw2: number;
      mape_pct: number;
      accuracy_pct: number;
      mean_bias_mw: number;
      r2_score: number;
    };
    trained_ml_ensemble_model: {
      mae_mw: number;
      rmse_mw: number;
      mse_mw2: number;
      mape_pct: number;
      accuracy_pct: number;
      mean_bias_mw: number;
      r2_score: number;
      mae_saved_mw: number;
      mae_reduction_pct: number;
      rmse_reduction_pct: number;
      mse_reduction_pct: number;
    };
  };
  cutoff_breakdown: CutoffPolicyItem[];
  selected_cutoff_daily_series: JanuaryDailyBenchmarkPoint[]; // 31 days
}

// ============================================================================
// 5. GET /api/v1/ingestion/coverage & POST /api/v1/ingestion/sync-date
// ============================================================================
export interface IngestionCoverageResponse {
  database_engine: string;
  windfor_stream: {
    total_revisions: number;
    distinct_delivery_days: number;
    min_delivery_date: string;
    max_delivery_date: string;
    min_publish_utc: string;
    max_publish_utc: string;
  };
  fuelhh_stream: {
    total_hourly_actuals: number;
    distinct_delivery_days: number;
    min_delivery_date: string;
    max_delivery_date: string;
  };
  chronological_splits: Array<{
    split_set: 'train' | 'val' | 'test' | 'live_bmrs' | string;
    start_date: string;
    end_date: string;
    days_count: number;
    hours_count: number;
  }>;
}

export interface IngestionSyncRequest {
  delivery_date: string;
  force_live_fetch?: boolean;
}

export interface IngestionSyncResponse {
  delivery_date: string;
  status: 'cached_in_database' | 'ingested_from_elexon_bmrs' | 'verified_in_database' | string;
  source: string;
  windfor_revisions_count: number;
  fuelhh_hours_count: number;
  live_api_called: boolean;
}
```

---

## 5. Endpoint-by-Endpoint Specification & Sample Payloads

### 5.1 `GET /api/v1/forecast/day-ahead` (Primary Trading Desk Endpoint)
Executes the full 4-stage pipeline for a single target `delivery_date` and Day-1 `cutoff_time`. Returns:
1. **`request_context`**: Metadata about the causal cutoff timestamp (`cutoff_publish_timestamp_utc`), the `[24, 9]` feature matrix shape, the cutoff's recommendation badge/rationale, and `.pkl` model metadata.
2. **`daily_kpis`**: Aggregate 24-hour MWh volumes, mean MW levels, Ex-Ante expected accuracy/MAE, and (when settled actuals exist) realized MAE, RMSE, accuracy %, MW error saved per hour, and empirical P10 hit rate.
3. **`hourly_slots` (24 objects)**: Full hourly data for `00:00` through `23:00 UTC`, including P10/P50/P90 MW values, all 9 trajectory features (`features`), the full chronological list of Day-1 Elexon revisions (`day1_revision_trajectory`), and realized actuals (`actual_wind_mw`).
4. **`day_cutoff_comparison` (8 objects)**: Summary metrics across all 8 Day-1 cutoffs (`02:30` to `22:30 UTC`) for that exact `delivery_date` so the frontend can plot the intraday cutoff trade-off curve without making 8 separate HTTP requests.

#### Query Parameters
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `delivery_date` | `string` (`YYYY-MM-DD`) | `"2026-01-15"` | Target UTC delivery date. Pre-seeded range: `2025-08-01` to `2026-01-31`. Out-of-sample test set: `2026-01-01` to `2026-01-31`. |
| `cutoff_time` | `CutoffTimeUtc` | `"15:30"` | Must be one of: `02:30`, `04:30`, `07:30`, `09:30`, `11:30`, `15:30`, `18:30`, `22:30`. |
| `auto_ingest_if_missing` | `boolean` | `true` | If `delivery_date` is not yet in PostgreSQL, automatically fetches it live from Elexon BMRS before running inference. |

#### Example Request
```bash
GET http://localhost:8000/api/v1/forecast/day-ahead?delivery_date=2026-01-15&cutoff_time=07:30
```

#### Example JSON Response (`200 OK`)
```json
{
  "request_context": {
    "delivery_date": "2026-01-15",
    "cutoff_time_utc": "07:30",
    "cutoff_publish_timestamp_utc": "2026-01-14T07:30:00Z",
    "matrix_shape": [24, 9],
    "cutoff_recommendation": {
      "cutoff_time_utc": "07:30",
      "avg_lead_time_hours": 28.0,
      "dec2025_val_mae_mw": 1368.5,
      "jan2026_untrained_latest_mae_mw": 2124.8,
      "jan2026_ml_mae_mw": 1164.2,
      "jan2026_mae_reduction_pct": 45.2,
      "expected_accuracy_pct": 86.8,
      "is_recommended": true,
      "is_primary_sweet_spot": false,
      "tranche_allocation_pct": 30,
      "tier_badge": "Recommended — Tranche 1 (Morning Open)",
      "trading_rationale": "First major NWP revision improvement (-118 MW vs 02:30). Optimal for locking initial 30% basal procurement."
    },
    "model_metadata": {
      "artifact_filename": "wind_procurement_model.pkl",
      "artifact_sha256": "c6f00817dd9634bf...",
      "model_architecture": "Residual-Learning Ensemble (65% HistGradientBoostingRegressor + 35% Ridge)",
      "training_window": "2025-08-01 to 2025-12-31 (29,376 rows — ZERO Jan 2026 Leakage)",
      "feature_count": 9,
      "feature_columns": [
        "latest_forecast",
        "first_forecast",
        "ewma_forecast",
        "mean_forecast",
        "std_forecast",
        "last_revision_delta",
        "overall_trend_slope",
        "prediction_count",
        "lead_time_hours"
      ]
    }
  },
  "daily_kpis": {
    "hours_count": 24,
    "mean_latest_forecast_mw": 9894.5,
    "mean_ml_predicted_mw": 8018.4,
    "mean_p10_safe_floor_mw": 6164.9,
    "mean_p90_upper_bound_mw": 9871.9,
    "total_daily_ml_mwh": 192442.0,
    "total_daily_safe_firm_mwh": 147958.0,
    "expected_mae_mw": 1278.3,
    "expected_accuracy_pct": 84.1,
    "mean_actual_wind_mw": 7711.8,
    "total_daily_actual_mwh": 185083.0,
    "realized_ml_mae_mw": 741.6,
    "realized_latest_mae_mw": 2182.7,
    "realized_ml_rmse_mw": 892.4,
    "realized_latest_rmse_mw": 2368.1,
    "realized_ml_accuracy_pct": 90.4,
    "realized_latest_accuracy_pct": 71.7,
    "mean_mw_error_saved_per_hour": 1441.1,
    "realized_mae_reduction_pct": 66.0,
    "empirical_p10_safe_floor_hit_rate_pct": 95.8,
    "empirical_p10_p90_band_coverage_pct": 91.7
  },
  "hourly_slots": [
    {
      "target_hour_utc": "00:00",
      "start_time_utc": "2026-01-15T00:00:00Z",
      "publish_time_utc": "2026-01-14T07:30:00Z",
      "cutoff_time_utc": "07:30",
      "lead_time_hours": 16.5,
      "latest_forecast_mw": 9420.0,
      "ml_predicted_mw": 7684.3,
      "ml_residual_adjustment_mw": -1735.7,
      "p10_safe_floor_mw": 5894.1,
      "p50_ml_forecast_mw": 7684.3,
      "p90_upper_bound_mw": 9474.5,
      "expected_error_mw": 1234.6,
      "expected_accuracy_pct": 83.9,
      "confidence_tier": "Moderate Confidence (Wide Hedge)",
      "features": {
        "latest_forecast": 9420.0,
        "first_forecast": 9810.0,
        "ewma_forecast": 9512.4,
        "mean_forecast": 9598.0,
        "std_forecast": 168.4,
        "last_revision_delta": -140.0,
        "overall_trend_slope": -24.8,
        "prediction_count": 5.0,
        "lead_time_hours": 16.5
      },
      "day1_revision_trajectory": [
        {
          "publish_time_utc": "2026-01-13T18:30:00Z",
          "forecast_mw": 9810.0,
          "lead_time_hours": 29.5
        },
        {
          "publish_time_utc": "2026-01-14T02:30:00Z",
          "forecast_mw": 9690.0,
          "lead_time_hours": 21.5
        },
        {
          "publish_time_utc": "2026-01-14T04:30:00Z",
          "forecast_mw": 9560.0,
          "lead_time_hours": 19.5
        },
        {
          "publish_time_utc": "2026-01-14T07:30:00Z",
          "forecast_mw": 9420.0,
          "lead_time_hours": 16.5
        }
      ],
      "actual_wind_mw": 7492.5,
      "rolling_7d_wind_mw": 8940.2,
      "realized_ml_error_mw": 191.8,
      "realized_latest_error_mw": 1927.5,
      "realized_ml_accuracy_pct": 97.4,
      "realized_latest_accuracy_pct": 74.3,
      "mw_error_saved_by_ml": 1735.7
    }
    // ... 23 more hourly objects ("01:00" through "23:00")
  ],
  "day_cutoff_comparison": [
    {
      "cutoff_time_utc": "02:30",
      "is_selected": false,
      "is_recommended": false,
      "is_primary_sweet_spot": false,
      "tier_badge": "Early Sighting — Hold Execution",
      "expected_accuracy_pct": 81.4,
      "expected_mae_mw": 1442.1,
      "mean_ml_predicted_mw": 8112.0,
      "mean_p10_safe_floor_mw": 6021.0,
      "realized_ml_mae_mw": 845.2,
      "realized_latest_mae_mw": 2310.4,
      "realized_ml_accuracy_pct": 89.0,
      "realized_latest_accuracy_pct": 70.0,
      "mean_mw_error_saved_per_hour": 1465.2
    }
    // ... all 8 cutoffs ("02:30", "04:30", "07:30", "09:30", "11:30", "15:30", "18:30", "22:30")
  ]
}
```

---

### 5.2 `GET /api/v1/cutoffs/policy` (Day-1 Cutoff Advisor & Tranche Policy)
Returns static and validation-calibrated policy metadata across all 8 Day-1 cutoffs (`02:30` to `22:30 UTC`), including the 3-tranche execution strategy (`30%` at `07:30`, `35%` at `11:30`, `35%` at `15:30 / 18:30`). Ideal for populating your cutoff selector dropdown, timeline stepper, or strategy card on initial app mount.

#### Example Request
```bash
GET http://localhost:8000/api/v1/cutoffs/policy
```

#### Example JSON Response (`200 OK`)
```json
{
  "total_daily_cutoffs": 8,
  "recommended_cutoffs_utc": ["07:30", "11:30", "15:30", "18:30"],
  "primary_sweet_spot_cutoffs_utc": ["15:30", "18:30"],
  "calibration_source": "2025-12-01 to 2025-12-31 Validation Holdout Set (n=5,952 — Zero Jan 2026 Leakage)",
  "recommended_execution_strategy": {
    "tranche_1": "07:30 UTC (30% volume) — First major error drop (-118 MW vs 02:30) at morning market open.",
    "tranche_2": "11:30 UTC (35% volume) — Pre-auction lock before midday Day-Ahead clearing.",
    "tranche_3_sweet_spot": "15:30 / 18:30 UTC (35% volume) — Primary accuracy sweet spot (>50.7% error reduction vs Latest)."
  },
  "cutoffs": [
    {
      "cutoff_time_utc": "02:30",
      "avg_lead_time_hours": 33.0,
      "dec2025_val_mae_mw": 1486.4,
      "jan2026_untrained_latest_mae_mw": 2258.4,
      "jan2026_ml_mae_mw": 1248.9,
      "jan2026_mae_reduction_pct": 44.7,
      "expected_accuracy_pct": 85.4,
      "is_recommended": false,
      "is_primary_sweet_spot": false,
      "tranche_allocation_pct": 0,
      "tier_badge": "Early Sighting — Hold Execution",
      "trading_rationale": "Longest lead time (~33h avg). High NWP atmospheric uncertainty; use for directional desk briefing only."
    },
    {
      "cutoff_time_utc": "07:30",
      "avg_lead_time_hours": 28.0,
      "dec2025_val_mae_mw": 1368.5,
      "jan2026_untrained_latest_mae_mw": 2124.8,
      "jan2026_ml_mae_mw": 1164.2,
      "jan2026_mae_reduction_pct": 45.2,
      "expected_accuracy_pct": 86.8,
      "is_recommended": true,
      "is_primary_sweet_spot": false,
      "tranche_allocation_pct": 30,
      "tier_badge": "Recommended — Tranche 1 (Morning Open)",
      "trading_rationale": "First major NWP revision improvement (-118 MW vs 02:30). Optimal for locking initial 30% basal procurement."
    },
    {
      "cutoff_time_utc": "15:30",
      "avg_lead_time_hours": 20.0,
      "dec2025_val_mae_mw": 1218.4,
      "jan2026_untrained_latest_mae_mw": 1962.1,
      "jan2026_ml_mae_mw": 1048.3,
      "jan2026_mae_reduction_pct": 46.6,
      "expected_accuracy_pct": 87.9,
      "is_recommended": true,
      "is_primary_sweet_spot": true,
      "tranche_allocation_pct": 35,
      "tier_badge": "Primary Sweet Spot — Tranche 3",
      "trading_rationale": "Optimal accuracy-to-liquidity inflection point. Captures afternoon synoptic updates while intraday liquidity remains deep."
    }
    // ... all 8 cutoffs
  ]
}
```

---

### 5.3 `GET /api/v1/benchmark/january-overview` (31-Day Out-of-Sample Evaluation)
Returns the full out-of-sample benchmark across all 31 unseen days of January 2026 (`5,952` Cutoff × Hour test samples), comparing the trained ML ensemble against the untrained Elexon `latest_forecast` baseline, plus a 31-day daily time series (`selected_cutoff_daily_series`) for the requested `cutoff_time`.

#### Query Parameters
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `cutoff_time` | `CutoffTimeUtc` | `"15:30"` | Day-1 cutoff used to compute the 31-day `selected_cutoff_daily_series` array. |

#### Example Request
```bash
GET http://localhost:8000/api/v1/benchmark/january-overview?cutoff_time=15:30
```

#### Example JSON Response (`200 OK`)
```json
{
  "evaluation_month": "2026-01 (Unseen Out-of-Sample Test Set)",
  "training_window_used": "2025-08-01 to 2025-12-31 ONLY (29,376 rows — ZERO Jan 2026 Leakage)",
  "total_test_samples": 5952,
  "overall_january_2026_metrics": {
    "untrained_latest_forecast": {
      "mae_mw": 2064.52,
      "rmse_mw": 2542.96,
      "mse_mw2": 6466644,
      "mape_pct": 20.49,
      "accuracy_pct": 79.51,
      "mean_bias_mw": 1810.69,
      "r2_score": 0.6249
    },
    "trained_ml_ensemble_model": {
      "mae_mw": 1120.78,
      "rmse_mw": 1442.87,
      "mse_mw2": 2081883,
      "mape_pct": 12.57,
      "accuracy_pct": 87.43,
      "mean_bias_mw": -160.92,
      "r2_score": 0.8792,
      "mae_saved_mw": 943.74,
      "mae_reduction_pct": 45.71,
      "rmse_reduction_pct": 43.26,
      "mse_reduction_pct": 67.81
    }
  },
  "cutoff_breakdown": [
    // Same 8 CutoffPolicyItem objects
  ],
  "selected_cutoff_daily_series": [
    {
      "delivery_date": "2026-01-01",
      "cutoff_time_utc": "15:30",
      "mean_actual_mw": 10842.3,
      "mean_latest_forecast_mw": 12614.8,
      "mean_ml_predicted_mw": 10695.1,
      "rolling_7d_wind_mw": 9412.0,
      "untrained_latest_mae_mw": 1812.4,
      "ml_model_mae_mw": 864.2,
      "mw_error_saved_per_hour": 948.2,
      "untrained_latest_accuracy_pct": 83.3,
      "ml_model_accuracy_pct": 92.0
    }
    // ... 31 daily objects ("2026-01-01" to "2026-01-31")
  ]
}
```

---

### 5.4 `POST /api/v1/ingestion/sync-date` (On-Demand Live Elexon BMRS Ingestion)
Verifies whether `delivery_date` is present in PostgreSQL (`raw_windfor_forecasts` and `raw_fuelhh_actuals`). If missing—or if `force_live_fetch: true` is passed—the backend queries the live UK Elexon Insights Solution REST API (`/bmrs/api/v1/datasets/WINDFOR/stream` and `/FUELHH/stream`), normalizes half-hourly settlement records into 1-hour UTC slots, and upserts them into PostgreSQL.

#### Request Body (`application/json`)
```json
{
  "delivery_date": "2026-01-15",
  "force_live_fetch": false
}
```

#### Example JSON Response (`200 OK`)
```json
{
  "delivery_date": "2026-01-15",
  "status": "cached_in_database",
  "source": "PostgreSQL (postgresql)",
  "windfor_revisions_count": 456,
  "fuelhh_hours_count": 24,
  "live_api_called": false
}
```

---

### 5.5 `GET /api/v1/ingestion/coverage` (Database Stream Coverage)
Returns the date boundaries (`min_delivery_date`, `max_delivery_date`), total row counts (`83,940` `WINDFOR` revisions and `4,416` `FUELHH` hourly actuals across `184` days), and the chronological train/validation/test split breakdown. Use this endpoint to configure the default min/max bounds or highlighted date badges in your React DatePicker.

#### Example JSON Response (`200 OK`)
```json
{
  "database_engine": "postgresql",
  "windfor_stream": {
    "total_revisions": 83940,
    "distinct_delivery_days": 184,
    "min_delivery_date": "2025-08-01",
    "max_delivery_date": "2026-01-31",
    "min_publish_utc": "2025-07-30T02:30:00Z",
    "max_publish_utc": "2026-01-30T22:30:00Z"
  },
  "fuelhh_stream": {
    "total_hourly_actuals": 4416,
    "distinct_delivery_days": 184,
    "min_delivery_date": "2025-08-01",
    "max_delivery_date": "2026-01-31"
  },
  "chronological_splits": [
    {
      "split_set": "train",
      "start_date": "2025-08-01",
      "end_date": "2025-11-30",
      "days_count": 122,
      "hours_count": 2928
    },
    {
      "split_set": "val",
      "start_date": "2025-12-01",
      "end_date": "2025-12-31",
      "days_count": 31,
      "hours_count": 744
    },
    {
      "split_set": "test",
      "start_date": "2026-01-01",
      "end_date": "2026-01-31",
      "days_count": 31,
      "hours_count": 744
    }
  ]
}
```

---

### 5.6 `GET /api/v1/system/health` (System Health & Model Integrity Check)
Returns PostgreSQL connectivity, table row counts, `.pkl` artifact SHA-256 hash, and anti-leakage verification status.

#### Example JSON Response (`200 OK`)
```json
{
  "status": "healthy",
  "service_name": "uk-wind-procurement-api",
  "backend_framework": "FastAPI + PostgreSQL 16",
  "database": {
    "connected": true,
    "engine": "postgresql",
    "raw_windfor_forecasts_rows": 83940,
    "raw_fuelhh_actuals_rows": 4416,
    "unseen_january_2026_days": 31
  },
  "ml_model": {
    "artifact_filename": "wind_procurement_model.pkl",
    "artifact_sha256": "c6f00817dd9634bf...",
    "feature_count": 9,
    "anti_leakage_verified": true
  },
  "anti_leakage_verified": true,
  "supported_cutoffs_utc": ["02:30", "04:30", "07:30", "09:30", "11:30", "15:30", "18:30", "22:30"],
  "recommended_cutoffs_utc": ["07:30", "11:30", "15:30", "18:30"]
}
```

---

## 6. Meaning of the 9 Trajectory Features (`slot.features`)

Every `HourlyForecastSlot` in `GET /api/v1/forecast/day-ahead` contains `slot.features` (the exact 9 inputs fed into `wind_procurement_model.pkl`). If your React UI includes an **Hourly Feature Inspector** or tooltip, here is what each feature represents physically and mathematically:

| Feature Key | Unit | Formula / Physical Meaning | Why the ML Model Uses It |
| :--- | :--- | :--- | :--- |
| `latest_forecast` | `MW` | Most recent Elexon `WINDFOR` forecast published $\le$ cutoff. | Primary baseline level before bias correction. |
| `first_forecast` | `MW` | Earliest Day-2 / early Day-1 `WINDFOR` forecast published for this hour. | Anchors where the meteorological run started 36–48h out. |
| `ewma_forecast` | `MW` | Exponentially Weighted Moving Average ($\alpha = 0.5$) of all revisions $\le$ cutoff. | Smooths out noisy single-run NWP oscillations. |
| `mean_forecast` | `MW` | Arithmetic mean of all revisions published $\le$ cutoff. | Captures central consensus across all revision runs. |
| `std_forecast` | `MW` | Sample standard deviation across revisions $\le$ cutoff. | Direct measure of weather front instability; drives the width of the `P10–P90` risk band. |
| `last_revision_delta` | `MW` | `latest_forecast - previous_forecast` (most recent step change). | Detects sudden downward or upward meteorological regime shifts right before the cutoff. |
| `overall_trend_slope` | `MW/hr` | `(latest_forecast - first_forecast) / hours_elapsed`. | Captures sustained directional drift across Day-2 and Day-1 updates. |
| `prediction_count` | `count` | Number of Elexon `WINDFOR` revisions published $\le$ cutoff. | Indicates information maturity at the selected cutoff. |
| `lead_time_hours` | `hours` | `(start_time_utc - publish_time_utc)` in hours (`3.5h` to `44.5h`). | Longer lead times carry systematically larger over-prediction bias and wider error bands. |

---

## 7. Recommended React Architecture, Custom Hooks & State Flows

### 7.1 Drop-In Typed React API Client & Hook (`src/hooks/useWindProcurement.ts`)
Here is a clean, production-ready React hook pattern your frontend team can use to manage Day-Ahead forecast fetching, cutoff switching, and live BMRS date synchronization:

```tsx
// src/hooks/useWindProcurement.ts
import { useState, useEffect, useCallback } from 'react';
import {
  CutoffTimeUtc,
  DayAheadForecastResponse,
  CutoffPolicyResponse,
  JanuaryBenchmarkResponse,
  IngestionSyncResponse,
} from '../types/windApi';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

export function useDayAheadForecast(
  deliveryDate: string,
  cutoffTime: CutoffTimeUtc
) {
  const [data, setData] = useState<DayAheadForecastResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [processTimeMs, setProcessTimeMs] = useState<string | null>(null);

  const fetchForecast = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        delivery_date: deliveryDate,
        cutoff_time: cutoffTime,
        auto_ingest_if_missing: 'true',
      });
      const res = await fetch(`${API_BASE}/api/v1/forecast/day-ahead?${params}`);
      const latency = res.headers.get('X-Process-Time-Ms');
      if (latency) setProcessTimeMs(latency);

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail || errBody.error || `HTTP ${res.status}`);
      }
      const json: DayAheadForecastResponse = await res.json();
      setData(json);
    } catch (err: any) {
      setError(err.message || 'Failed to load day-ahead forecast');
    } finally {
      setLoading(false);
    }
  }, [deliveryDate, cutoffTime]);

  useEffect(() => {
    fetchForecast();
  }, [fetchForecast]);

  const syncLiveFromElexon = useCallback(async (): Promise<IngestionSyncResponse> => {
    const res = await fetch(`${API_BASE}/api/v1/ingestion/sync-date`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        delivery_date: deliveryDate,
        force_live_fetch: true,
      }),
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.detail || errBody.error || `Sync failed (${res.status})`);
    }
    const syncResult: IngestionSyncResponse = await res.json();
    await fetchForecast();
    return syncResult;
  }, [deliveryDate, fetchForecast]);

  return {
    data,
    loading,
    error,
    processTimeMs,
    refetch: fetchForecast,
    syncLiveFromElexon,
  };
}
```

### 7.2 Recommended React State Flow
To keep the user experience fast and intuitive, we recommend organizing React state around **four primary interactive controls**:

1. **`deliveryDate` (`string`, default `"2026-01-15"`)**:
   - Dates in **`2026-01-01` .. `2026-01-31`** represent the **Unseen Out-of-Sample Test Set** (both Ex-Ante predictions and Ex-Post realized `FUELHH` actuals exist, with zero training leakage).
   - Dates in **`2025-08-01` .. `2025-12-31`** represent the historical Training / Validation period.
   - Any other recent/live date triggers `auto_ingest_if_missing=true` (or can be explicitly refreshed via `POST /api/v1/ingestion/sync-date`).
2. **`cutoffTime` (`CutoffTimeUtc`, default `"15:30"` or `"07:30"`)**:
   - Changing `cutoffTime` re-queries `GET /api/v1/forecast/day-ahead?delivery_date=...&cutoff_time=...` (`~15 ms` response time).
   - Notice that `data.day_cutoff_comparison` in the response already contains the summary metrics for all 8 cutoffs on that date—so your **8-Cutoff Trade-Off Chart** does not need to fire 8 separate requests!
3. **`selectedHourUtc` (`string`, default `"00:00"` or clicked chart point)**:
   - Clicking any hour (`00:00` to `23:00`) on the 24-hour chart or table selects that `HourlyForecastSlot` in local React state (`const selectedSlot = data?.hourly_slots.find(s => s.target_hour_utc === selectedHourUtc)`).
   - This lets you render an **Hourly Revision Trajectory Mini-Chart** (`selectedSlot.day1_revision_trajectory`) and a **9-Feature Breakdown Panel** (`selectedSlot.features`) instantaneously with zero network calls.
4. **`showRealizedActuals` (`boolean`, default `true` when `data?.daily_kpis.mean_actual_wind_mw !== undefined`)**:
   - Allows traders to toggle between **"Live Day-1 Ex-Ante View"** (hiding actuals to simulate making a decision on Day-1 before delivery) and **"Post-Settlement Audit View"** (overlaying `actual_wind_mw` and `mw_error_saved_by_ml`).

---

## 8. Suggested Visualizations & UI Components (Design Inspiration)

Visual design, layout, color palette, and charting library choices (e.g., Recharts, Visx, ECharts, Chart.js, or D3) are **100% up to the frontend team**. Below are the high-value visualizations and data views that energy traders expect in a Day-Ahead Wind Procurement workstation:

### 8.1 Top KPI Ribbon (`data.daily_kpis` + `data.request_context`)
Display 4 to 5 scannable metric cards summarizing the 24-hour delivery day at the selected Day-1 cutoff:
- **Daily P50 ML Forecast Volume**: `daily_kpis.total_daily_ml_mwh` MWh (with mean hourly `daily_kpis.mean_ml_predicted_mw` MW).
- **Daily P10 Safe Firm Floor**: `daily_kpis.total_daily_safe_firm_mwh` MWh (with mean hourly `daily_kpis.mean_p10_safe_floor_mw` MW — the volume safe to lock in without imbalance risk).
- **Ex-Ante Expected Accuracy**: `daily_kpis.expected_accuracy_pct`% (`± daily_kpis.expected_mae_mw` MW expected error).
- **Realized ML Accuracy vs Raw Elexon** *(when actuals exist)*: `daily_kpis.realized_ml_accuracy_pct`% vs `daily_kpis.realized_latest_accuracy_pct`%, highlighting **`daily_kpis.mean_mw_error_saved_per_hour` MW/hr saved** (`daily_kpis.realized_mae_reduction_pct`% MAE reduction).
- **Cutoff Tier Badge**: `request_context.cutoff_recommendation.tier_badge` and `tranche_allocation_pct`% recommendation.

### 8.2 Main Chart: 24-Hour Probabilistic Procurement Curve (`data.hourly_slots`)
An interactive 24-hour (`00:00` to `23:00 UTC`) composite Area + Line chart:
- **Shaded Confidence Band (`Area`)**: Fill between `p10_safe_floor_mw` (bottom edge) and `p90_upper_bound_mw` (top edge) to visualize hourly atmospheric uncertainty.
- **P10 Safe Firm Floor Line (`Line`)**: Distinct lower boundary line (`p10_safe_floor_mw`) showing firm committable capacity.
- **Central ML Forecast (`Line` — Primary Emphasis)**: Bold curve for `p50_ml_forecast_mw`.
- **Raw Elexon Baseline (`Dashed Line`)**: `latest_forecast_mw` so traders immediately see the gap (the `ml_residual_adjustment_mw` bias correction).
- **Settled Actual Wind (`Line` or `Scatter Dots`, conditional)**: `actual_wind_mw` when available.
- **Interaction**: Hovering or clicking an hour (`00:00`–`23:00`) highlights that slot and updates the **Hourly Inspector Panel**.

### 8.3 Secondary Chart: Day-1 8-Cutoff Accuracy & Volume Trade-Off (`data.day_cutoff_comparison`)
A bar or line chart across the 8 Day-1 cutoffs (`02:30`, `04:30`, `07:30`, `09:30`, `11:30`, `15:30`, `18:30`, `22:30 UTC`):
- Plots `expected_mae_mw` (and `realized_ml_mae_mw` vs `realized_latest_mae_mw` when available) across the 8 cutoffs.
- Visually highlights the **4 recommended cutoffs** (`is_recommended: true`) and the **primary sweet spot** (`15:30` and `18:30`, `is_primary_sweet_spot: true`).
- Clicking any cutoff bar/point on this chart can directly update `cutoffTime` in React state.

### 8.4 Hourly Trajectory & 9-Feature Inspector (`selectedSlot`)
When a user selects a specific hour (e.g., `14:00 UTC`):
1. **Day-1 Revision Trajectory Mini-Chart**: Plot `selectedSlot.day1_revision_trajectory` (`publish_time_utc` on X-axis vs `forecast_mw` on Y-axis) showing how Elexon revised its forecast for that specific hour across Day-2 and Day-1 up to the cutoff, alongside the final `p50_ml_forecast_mw` and `p10_safe_floor_mw`.
2. **9 Trajectory Features Card**: Display `selectedSlot.features` (`latest_forecast`, `first_forecast`, `ewma_forecast`, `mean_forecast`, `std_forecast`, `last_revision_delta`, `overall_trend_slope`, `prediction_count`, `lead_time_hours`) and the signed `ml_residual_adjustment_mw`.

### 8.5 24-Hour Procurement Schedule Table (`data.hourly_slots`)
A clean, exportable 24-row table (`00:00` to `23:00 UTC`) for trading desk execution showing:
- `target_hour_utc` & `lead_time_hours`
- `latest_forecast_mw` (Raw Elexon)
- `ml_residual_adjustment_mw` (ML Bias Correction)
- `p50_ml_forecast_mw` (ML Central Forecast)
- `p10_safe_floor_mw` (90% Safe Firm Floor)
- `expected_accuracy_pct` & `confidence_tier` badge
- `actual_wind_mw` & `mw_error_saved_by_ml` *(when settled actuals exist)*

### 8.6 Out-of-Sample Benchmark View (`GET /api/v1/benchmark/january-overview`)
A dedicated tab or drawer proving model reliability across all 31 unseen days of January 2026 (`5,952` evaluations):
- Compares overall Untrained `latest_forecast` (`2,064.52 MW` MAE) vs. Trained ML Ensemble (`1,120.78 MW` MAE — **`45.71%` error reduction**, **`R² = 0.8792`**).
- Plots the 31-day time series (`selected_cutoff_daily_series`) comparing daily `untrained_latest_mae_mw` vs. `ml_model_mae_mw` and `mw_error_saved_per_hour`.

---

## 9. Quick Verification Checklist for Frontend Developers

1. **Start the Backend**:
   ```bash
   docker compose up -d --build
   ```
   - Backend API: `http://localhost:8000`
   - Interactive Swagger UI: `http://localhost:8000/docs`
2. **Test Health Endpoint**:
   ```bash
   curl -s http://localhost:8000/api/v1/system/health | jq .status
   # Expected: "healthy"
   ```
3. **Test 24-Hour Forecast Endpoint**:
   ```bash
   curl -s "http://localhost:8000/api/v1/forecast/day-ahead?delivery_date=2026-01-15&cutoff_time=15:30" | jq '.daily_kpis'
   ```
4. **Verify Edge-Case Error Handling**:
   - Passing an invalid cutoff (`cutoff_time=08:00`) returns `HTTP 400` with `{ "detail": "Invalid cutoff_time_utc '08:00'..." }`.
   - Passing a date always returns `hourly_slots.length === 24` and `day_cutoff_comparison.length === 8`.
