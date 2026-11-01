-- =============================================================================
-- UK Day-Ahead Wind Procurement Platform — PostgreSQL 16 Production Schema
-- Automatically mounted into /docker-entrypoint-initdb.d/01_schema.sql
-- =============================================================================

-- 1. Raw Elexon WINDFOR Forecast Stream Table
-- Stores raw Elexon revision records published across Day-2 and Day-1 windows.
-- The feature extraction service computes all 9 trajectory features dynamically
-- at query time subject to the causal filter (publish_time_utc <= cutoff_ts).
CREATE TABLE IF NOT EXISTS raw_windfor_forecasts (
    id SERIAL PRIMARY KEY,
    start_time_utc VARCHAR(20) NOT NULL,         -- ISO-8601 target 1-hour slot start (e.g., 2026-01-15T14:00:00Z)
    publish_time_utc VARCHAR(20) NOT NULL,       -- ISO-8601 Elexon batch publish timestamp (e.g., 2026-01-14T07:30:00Z)
    delivery_date VARCHAR(10) NOT NULL,          -- Target UTC delivery date (YYYY-MM-DD)
    target_hour_utc VARCHAR(5) NOT NULL,         -- Target hour slot (00:00 to 23:00 UTC)
    forecast_mw DOUBLE PRECISION NOT NULL,       -- Raw forecasted wind generation (MW)
    UNIQUE (start_time_utc, publish_time_utc)
);

-- Composite B-Tree index optimized for causal Day-1 cutoff queries:
-- WHERE delivery_date = :date AND publish_time_utc <= :cutoff_ts ORDER BY target_hour_utc, publish_time_utc
CREATE INDEX IF NOT EXISTS idx_windfor_causal_lookup
    ON raw_windfor_forecasts (delivery_date, publish_time_utc, target_hour_utc);

CREATE INDEX IF NOT EXISTS idx_windfor_start_pub
    ON raw_windfor_forecasts (start_time_utc, publish_time_utc);


-- 2. Raw Elexon FUELHH Wind Generation Settlement Stream Table (Aligned to 1-Hour Slots)
CREATE TABLE IF NOT EXISTS raw_fuelhh_actuals (
    start_time_utc VARCHAR(20) PRIMARY KEY,      -- ISO-8601 target 1-hour slot start (e.g., 2026-01-15T14:00:00Z)
    delivery_date VARCHAR(10) NOT NULL,          -- YYYY-MM-DD
    month VARCHAR(7) NOT NULL,                   -- YYYY-MM
    target_hour_utc VARCHAR(5) NOT NULL,         -- HH:00 (00:00 to 23:00 UTC)
    split_set VARCHAR(24) NOT NULL,              -- train_aug_oct | val_nov | test_dec | eval_jan2026 | live_bmrs
    slot_00_mw DOUBLE PRECISION NOT NULL,        -- 30-min FUELHH settlement period at HH:00 (MW)
    slot_30_mw DOUBLE PRECISION NOT NULL,        -- 30-min FUELHH settlement period at HH:30 (MW)
    actual_wind_mw DOUBLE PRECISION NOT NULL,    -- Hourly average (slot_00_mw + slot_30_mw) / 2.0 (MW)
    rolling_7d_wind_mw DOUBLE PRECISION NOT NULL,-- Causal 7-day rolling baseline (D-8 to D-2 MW)
    seasonal_regime_ratio DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fuelhh_delivery_date
    ON raw_fuelhh_actuals (delivery_date, target_hour_utc);
