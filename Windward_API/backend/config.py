"""
Central configuration for the FastAPI + PostgreSQL Wind Procurement Backend.
"""

import os

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT_DIR, "data")

# Default host port is 5433 (to avoid conflicts with local port 5432);
# inside Docker Compose, DATABASE_URL is set to postgresql://postgres:postgres@db:5432/wind_procurement
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/wind_procurement",
)

# When DATABASE_URL is unreachable the app degrades to the local SQLite replica so
# that local development keeps working. In production that fallback is dangerous:
# the service stays "healthy" while writing to an ephemeral disk. Set
# ALLOW_SQLITE_FALLBACK=false there so a bad DATABASE_URL fails loudly instead.
ALLOW_SQLITE_FALLBACK = os.getenv("ALLOW_SQLITE_FALLBACK", "true").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
SQLITE_FALLBACK_PATH = os.path.join(DATA_DIR, "wind_procurement.db")

MODEL_PKL_PATH = os.getenv(
    "MODEL_PKL_PATH",
    os.path.join(ROOT_DIR, "wind_procurement_model.pkl"),
)
AUDIT_CSV_PATH = os.path.join(DATA_DIR, "elexon_wind_full_audit.csv")

ELEXON_API_BASE = os.getenv(
    "ELEXON_API_BASE",
    "https://data.elexon.co.uk/bmrs/api/v1/datasets",
)

VALID_CUTOFFS_UTC = [
    "02:30",
    "04:30",
    "07:30",
    "09:30",
    "11:30",
    "15:30",
    "18:30",
    "22:30",
]

RECOMMENDED_CUTOFFS_UTC = ["07:30", "11:30", "15:30", "18:30"]
PRIMARY_SWEET_SPOT_CUTOFFS_UTC = ["15:30", "18:30"]

FEATURE_COLUMNS = [
    "latest_forecast",
    "first_forecast",
    "ewma_forecast",
    "mean_forecast",
    "std_forecast",
    "last_revision_delta",
    "overall_trend_slope",
    "prediction_count",
    "lead_time_hours",
]
TARGET_COLUMN = "target_actual_mw"
EWMA_ALPHA = 0.40
