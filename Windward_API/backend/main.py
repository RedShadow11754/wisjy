#!/usr/bin/env python3
"""
FastAPI Application Entrypoint (backend/main.py).

Supports:
  1. Production Uvicorn / Docker server execution:
       `uvicorn backend.main:app --host 0.0.0.0 --port 8000`
  2. Direct CLI JSON invocation for gateway bridges and diagnostics:
       `python3 -m backend.main --endpoint health`
       `python3 -m backend.main --endpoint cutoffs`
       `python3 -m backend.main --endpoint forecast --date 2026-01-15 --cutoff 07:30`
       `python3 -m backend.main --endpoint benchmark --cutoff 15:30`
       `python3 -m backend.main --endpoint coverage`
       `python3 -m backend.main --endpoint sync --date 2026-01-15`
"""

import argparse
import json
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

from backend.db.database import DatabaseConnection
from backend.db.seed_db import seed_database
from backend.routers.benchmark import build_january_benchmark_payload
from backend.routers.cutoffs import build_cutoff_policy_payload
from backend.routers.forecast import build_day_ahead_forecast_payload
from backend.routers.health import build_health_payload
from backend.services.elexon_ingestor import (
    get_database_coverage_summary,
    sync_delivery_date_from_elexon,
)


try:
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from backend.routers import benchmark, cutoffs, forecast, health, ingestion

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Ensure PostgreSQL tables are initialized and seeded on startup
        seed_database(force_reseed=False)
        yield

    app = FastAPI(
        title="UK Day-Ahead Wind Procurement API",
        description=(
            "Production FastAPI + PostgreSQL 16 backend executing causal 24-hour trajectory "
            "feature extraction, vectorized wind_procurement_model.pkl ensemble inference "
            "(trained strictly on Aug–Dec 2025 with zero January 2026 leakage), "
            "ex-ante P10/P50/P90 risk estimation, and live Elexon BMRS synchronization."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
        return response

    @app.get("/", tags=["Root Discovery"])
    def get_api_root() -> Dict[str, Any]:
        return {
            "service": "UK Day-Ahead Wind Procurement API",
            "version": "1.0.0",
            "docs_url": "/docs",
            "openapi_url": "/openapi.json",
            "endpoints": {
                "health": "/api/v1/system/health",
                "cutoff_policy": "/api/v1/cutoffs/policy",
                "day_ahead_forecast": "/api/v1/forecast/day-ahead?delivery_date=2026-01-15&cutoff_time=15:30",
                "january_benchmark": "/api/v1/benchmark/january-overview?cutoff_time=15:30",
                "ingestion_coverage": "/api/v1/ingestion/coverage",
                "ingestion_sync_date": "/api/v1/ingestion/sync-date",
            },
        }

    for mod in (health, cutoffs, forecast, benchmark, ingestion):
        if getattr(mod, "router", None) is not None:
            app.include_router(mod.router)

except ImportError:
    app = None


def dispatch_cli_request(
    endpoint: str,
    delivery_date: str,
    cutoff_time: str,
    force_live: bool = False,
) -> Dict[str, Any]:
    seed_database(force_reseed=False)
    db = DatabaseConnection()
    try:
        if endpoint == "health":
            return build_health_payload(db)
        if endpoint == "cutoffs":
            return build_cutoff_policy_payload()
        if endpoint == "forecast":
            return build_day_ahead_forecast_payload(db, delivery_date, cutoff_time)
        if endpoint == "benchmark":
            return build_january_benchmark_payload(db, cutoff_time)
        if endpoint == "coverage":
            return get_database_coverage_summary(db)
        if endpoint == "sync":
            return sync_delivery_date_from_elexon(db, delivery_date, force_live_fetch=force_live)
        raise ValueError(f"Unknown endpoint: {endpoint}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UK Wind Procurement FastAPI Dispatcher")
    parser.add_argument(
        "--endpoint",
        required=True,
        choices=["health", "cutoffs", "forecast", "benchmark", "coverage", "sync"],
    )
    parser.add_argument("--date", default="2026-01-15")
    parser.add_argument("--cutoff", default="15:30")
    parser.add_argument("--force-live", action="store_true")
    args = parser.parse_args()

    try:
        payload = dispatch_cli_request(args.endpoint, args.date, args.cutoff, args.force_live)
        sys.stdout.write(json.dumps(payload))
    except Exception as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}))
        sys.exit(1)
