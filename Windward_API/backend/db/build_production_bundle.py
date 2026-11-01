#!/usr/bin/env python3
"""
Production Docker Bundle Builder (backend/db/build_production_bundle.py).

Packages the complete, clean production UK Day-Ahead Wind Procurement Backend into
`/elexon_wind_backend_docker.zip` ready for one-click download and execution in Docker Desktop:
  - docker-compose.yml (PostgreSQL 16 on 5433, FastAPI Uvicorn on 8000, pgAdmin 4 on 5050)
  - Dockerfile.backend
  - requirements.txt
  - workflow.md
  - wind_procurement_model.pkl
  - data/raw_windfor_stream.csv, data/raw_fuelhh_stream.csv, data/elexon_wind_full_audit.csv
  - backend/* (config, main, schemas, cli_predict, db, services, routers, tests)
"""

import os
import zipfile

from backend.config import ROOT_DIR

BUNDLE_ZIP_PATH = os.path.join(ROOT_DIR, "elexon_wind_backend_docker.zip")

FILES_TO_PACKAGE = [
    "docker-compose.yml",
    "Dockerfile.backend",
    "requirements.txt",
    "workflow.md",
    "wind_procurement_model.pkl",
    "data/raw_windfor_stream.csv",
    "data/raw_fuelhh_stream.csv",
    "data/elexon_wind_full_audit.csv",
    "backend/__init__.py",
    "backend/config.py",
    "backend/main.py",
    "backend/schemas.py",
    "backend/cli_predict.py",
    "backend/db/__init__.py",
    "backend/db/schema.sql",
    "backend/db/models.py",
    "backend/db/database.py",
    "backend/db/seed_db.py",
    "backend/db/build_production_bundle.py",
    "backend/services/__init__.py",
    "backend/services/feature_extractor.py",
    "backend/services/model_predictor.py",
    "backend/services/accuracy_estimator.py",
    "backend/services/cutoff_advisor.py",
    "backend/services/elexon_ingestor.py",
    "backend/routers/__init__.py",
    "backend/routers/health.py",
    "backend/routers/cutoffs.py",
    "backend/routers/forecast.py",
    "backend/routers/benchmark.py",
    "backend/routers/ingestion.py",
    "backend/tests/__init__.py",
    "backend/tests/test_production_suite.py",
]


def build_production_zip() -> str:
    with zipfile.ZipFile(BUNDLE_ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in FILES_TO_PACKAGE:
            abs_path = os.path.join(ROOT_DIR, rel_path)
            if not os.path.exists(abs_path):
                raise FileNotFoundError(f"Required production file missing: {abs_path}")
            zf.write(abs_path, arcname=rel_path)

    size_mb = os.path.getsize(BUNDLE_ZIP_PATH) / (1024 * 1024)
    print(f"[Production Docker Bundle Built] {BUNDLE_ZIP_PATH} ({size_mb:.2f} MB, {len(FILES_TO_PACKAGE)} files)")
    return BUNDLE_ZIP_PATH


if __name__ == "__main__":
    build_production_zip()
