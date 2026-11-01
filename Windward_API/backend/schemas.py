"""
Pydantic v2 Request & Response Schemas for the UK Day-Ahead Wind Procurement FastAPI Backend.
Provides strict type validation and OpenAPI 3.1 schema documentation for Swagger UI (/docs).
"""

from typing import Any, Dict, List, Optional

try:
    from pydantic import BaseModel, Field

    class DatabaseStatusSchema(BaseModel):
        connected: bool = Field(..., description="True when database connection is active")
        engine: str = Field(..., description="Active relational engine (postgresql or sqlite fallback)")
        raw_windfor_forecasts_rows: int = Field(..., description="Total raw WINDFOR forecast revisions stored")
        raw_fuelhh_actuals_rows: int = Field(..., description="Total hourly FUELHH settlement records stored")
        unseen_january_2026_days: int = Field(..., description="Count of out-of-sample January 2026 days available")

    class SystemHealthResponseSchema(BaseModel):
        status: str = Field(..., example="healthy")
        service_name: str = Field(..., example="uk-wind-procurement-api")
        backend_framework: str = Field(..., example="FastAPI + PostgreSQL 16")
        database: DatabaseStatusSchema
        ml_model: Dict[str, Any]
        anti_leakage_verified: bool = Field(..., description="Confirms zero January 2026 rows were used in training")
        supported_cutoffs_utc: List[str]
        recommended_cutoffs_utc: List[str]

    class CutoffPolicyItemSchema(BaseModel):
        cutoff_time_utc: str = Field(..., description="Day-1 cutoff time in UTC (HH:MM)")
        avg_lead_time_hours: float
        dec2025_val_mae_mw: float
        jan2026_untrained_latest_mae_mw: float
        jan2026_ml_mae_mw: float
        jan2026_mae_reduction_pct: float
        expected_accuracy_pct: float
        is_recommended: bool
        is_primary_sweet_spot: bool
        tranche_allocation_pct: int = 0
        tier_badge: str
        trading_rationale: str

    class CutoffPolicyResponseSchema(BaseModel):
        total_daily_cutoffs: int
        recommended_cutoffs_utc: List[str]
        primary_sweet_spot_cutoffs_utc: List[str]
        calibration_source: str
        recommended_execution_strategy: Dict[str, str]
        cutoffs: List[CutoffPolicyItemSchema]

    class HourlyForecastSlotSchema(BaseModel):
        target_hour_utc: str
        start_time_utc: str
        publish_time_utc: str
        cutoff_time_utc: str
        lead_time_hours: float
        latest_forecast_mw: float
        ml_predicted_mw: float
        ml_residual_adjustment_mw: float
        p10_safe_floor_mw: float
        p50_ml_forecast_mw: float
        p90_upper_bound_mw: float
        expected_error_mw: float
        expected_accuracy_pct: float
        confidence_tier: str
        features: Dict[str, float]
        day1_revision_trajectory: List[Dict[str, Any]]
        actual_wind_mw: Optional[float] = None
        rolling_7d_wind_mw: Optional[float] = None
        realized_ml_error_mw: Optional[float] = None
        realized_latest_error_mw: Optional[float] = None
        realized_ml_accuracy_pct: Optional[float] = None
        realized_latest_accuracy_pct: Optional[float] = None
        mw_error_saved_by_ml: Optional[float] = None

    class DayAheadForecastResponseSchema(BaseModel):
        request_context: Dict[str, Any]
        daily_kpis: Dict[str, Any]
        hourly_slots: List[HourlyForecastSlotSchema]
        day_cutoff_comparison: List[Dict[str, Any]]

    class JanuaryBenchmarkResponseSchema(BaseModel):
        evaluation_month: str
        training_window_used: str
        total_test_samples: int
        overall_january_2026_metrics: Dict[str, Any]
        cutoff_breakdown: List[CutoffPolicyItemSchema]
        selected_cutoff_daily_series: List[Dict[str, Any]]

    class IngestionSyncRequestSchema(BaseModel):
        delivery_date: str = Field("2026-01-15", description="Target UTC delivery date (YYYY-MM-DD) to verify or ingest from Elexon BMRS")
        force_live_fetch: bool = Field(False, description="If true, queries live Elexon BMRS API even when date exists in PostgreSQL")

    class IngestionSyncResponseSchema(BaseModel):
        delivery_date: str
        status: str
        source: str
        windfor_revisions_count: int
        fuelhh_hours_count: int
        live_api_called: bool

except ImportError:
    DatabaseStatusSchema = dict  # type: ignore
    SystemHealthResponseSchema = dict  # type: ignore
    CutoffPolicyItemSchema = dict  # type: ignore
    CutoffPolicyResponseSchema = dict  # type: ignore
    HourlyForecastSlotSchema = dict  # type: ignore
    DayAheadForecastResponseSchema = dict  # type: ignore
    JanuaryBenchmarkResponseSchema = dict  # type: ignore
    IngestionSyncRequestSchema = dict  # type: ignore
    IngestionSyncResponseSchema = dict  # type: ignore
