/* ── Windboard API Type Definitions ── */

export type CutoffTimeUtc =
  | '02:30' | '04:30' | '07:30' | '09:30'
  | '11:30' | '15:30' | '18:30' | '22:30';

export const ALL_CUTOFFS: CutoffTimeUtc[] = [
  '02:30','04:30','07:30','09:30','11:30','15:30','18:30','22:30',
];

/* ── System Health ── */
export interface SystemHealthResponse {
  status: string;
  database_connected: boolean;
  windfor_row_count: number;
  fuelhh_row_count: number;
  model_loaded: boolean;
  model_features: string[];
  model_type: string;
  training_split: { start: string; end: string; sample_count: number };
  anti_leakage_verified: boolean;
  anti_leakage_detail: string;
}

/* ── Cutoff Policy ── */
export interface CutoffPolicyItem {
  cutoff_time_utc: CutoffTimeUtc;
  window_label: string;
  expected_mae_mw: number;
  expected_accuracy_pct: number;
  liquidity_tier: string;
  recommended_role: string;
  explanation: string;
}

export interface TrancheRecommendation {
  tranche_label: string;
  allocation_pct: number;
  cutoff_time_utc: CutoffTimeUtc;
  role: string;
  explanation: string;
}

export interface CutoffPolicyResponse {
  delivery_scope: string;
  evaluation_basis: string;
  cutoff_windows: CutoffPolicyItem[];
  recommended_execution_strategy: {
    strategy_label: string;
    rationale: string;
    tranches: TrancheRecommendation[];
  };
}

/* ── Day-Ahead Forecast ── */
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
}

export interface HourlyForecastSlot {
  target_hour_utc: string;
  p10_mw: number;
  p50_mw: number;
  p90_mw: number;
  latest_forecast_mw: number;
  ml_predicted_mw: number;
  ml_residual_adjustment_mw: number;
  expected_error_mw: number;
  expected_accuracy_pct: number;
  confidence_tier: string;
  nine_trajectory_features: NineTrajectoryFeatures;
  revision_trajectory: RevisionTrajectoryPoint[];
  actual_wind_mw?: number | null;
  realized_ml_error_mw?: number | null;
  realized_latest_error_mw?: number | null;
  realized_ml_accuracy_pct?: number | null;
  realized_latest_accuracy_pct?: number | null;
  mw_error_saved_by_ml?: number | null;
}

export interface DailyForecastKpis {
  delivery_date: string;
  cutoff_time_utc: string;
  mean_p50_mw: number;
  mean_latest_forecast_mw: number;
  mean_ml_predicted_mw: number;
  total_p50_mwh: number;
  total_latest_mwh: number;
  total_ml_predicted_mwh: number;
  mean_expected_accuracy_pct: number;
  mean_expected_error_mw: number;
  hours_with_actuals: number;
  mean_realized_ml_accuracy_pct?: number | null;
  mean_realized_latest_accuracy_pct?: number | null;
  mean_realized_ml_mae_mw?: number | null;
  mean_realized_latest_mae_mw?: number | null;
  mean_mw_error_saved_by_ml?: number | null;
  total_actual_mwh?: number | null;
}

export interface ModelMetadata {
  model_type: string;
  feature_count: number;
  features: string[];
  training_split: string;
  training_sample_count: number;
}

export interface CutoffRecommendation {
  cutoff_time_utc: CutoffTimeUtc;
  window_label: string;
  expected_mae_mw: number;
  expected_accuracy_pct: number;
  liquidity_tier: string;
  recommended_role: string;
  explanation: string;
}

export interface RequestContext {
  delivery_date: string;
  cutoff_time_utc: string;
  cutoff_publish_timestamp_utc: string;
  matrix_shape: [number, number];
  cutoff_recommendation: CutoffRecommendation;
  model_metadata: ModelMetadata;
}

export interface DayCutoffComparisonItem {
  cutoff_time_utc: CutoffTimeUtc;
  window_label: string;
  mean_ml_predicted_mw: number;
  mean_latest_forecast_mw: number;
  mean_expected_error_mw: number;
  mean_expected_accuracy_pct: number;
  total_ml_predicted_mwh: number;
  mean_realized_ml_mae_mw?: number | null;
  mean_realized_latest_mae_mw?: number | null;
  mean_mw_saved_by_ml?: number | null;
}

export interface DayAheadForecastResponse {
  request_context: RequestContext;
  daily_kpis: DailyForecastKpis;
  hourly_slots: HourlyForecastSlot[];
  day_cutoff_comparison: DayCutoffComparisonItem[];
}

/* ── Benchmark ── */
export interface DailyBenchmarkRecord {
  delivery_date: string;
  ml_mae_mw: number;
  latest_mae_mw: number;
  ml_mean_predicted_mw: number;
  latest_mean_forecast_mw: number;
  actual_mean_mw: number;
  mw_saved_by_ml: number;
  ml_accuracy_pct: number;
  latest_accuracy_pct: number;
}

export interface JanuaryBenchmarkResponse {
  benchmark_scope: string;
  cutoff_time_utc: CutoffTimeUtc;
  total_test_samples: number;
  aggregate_metrics: {
    ml_mae_mw: number;
    latest_mae_mw: number;
    mae_reduction_pct: number;
    ml_rmse_mw: number;
    latest_rmse_mw: number;
    ml_r2: number;
    latest_r2: number;
    ml_mean_accuracy_pct: number;
    latest_mean_accuracy_pct: number;
  };
  daily_records: DailyBenchmarkRecord[];
  training_integrity: string;
}

/* ── Ingestion ── */
export interface StreamCoverage {
  stream: string;
  earliest_date: string;
  latest_date: string;
  row_count: number;
}

export interface ChronologicalSplit {
  split: string;
  start_date: string;
  end_date: string;
  sample_count: number;
}

export interface IngestionCoverageResponse {
  streams: StreamCoverage[];
  chronological_splits: ChronologicalSplit[];
  total_delivery_dates: number;
}

export interface IngestionSyncResponse {
  delivery_date: string;
  windfor_rows_inserted: number;
  fuelhh_rows_inserted: number;
  source: string;
  cached_date_used: string | null;
}
