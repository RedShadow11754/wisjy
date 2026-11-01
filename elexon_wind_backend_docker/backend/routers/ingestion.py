"""
Elexon BMRS Live Ingestion & Coverage Router (/api/v1/ingestion/*).
Allows inspecting database stream coverage and triggering on-demand live
Elexon BMRS `WINDFOR` and `FUELHH` synchronization into PostgreSQL.
"""

from typing import Any, Dict

from backend.db.database import DatabaseConnection
from backend.services.elexon_ingestor import (
    get_database_coverage_summary,
    sync_delivery_date_from_elexon,
)


try:
    from fastapi import APIRouter, Body, Depends, HTTPException
    from backend.db.database import get_db
    from backend.schemas import IngestionSyncRequestSchema, IngestionSyncResponseSchema

    router = APIRouter(prefix="/api/v1/ingestion", tags=["Elexon BMRS Ingestion & Coverage"])

    @router.get(
        "/coverage",
        summary="Inspect WINDFOR and FUELHH date range and chronological split coverage in PostgreSQL",
    )
    def get_ingestion_coverage(db: DatabaseConnection = Depends(get_db)) -> Dict[str, Any]:
        return get_database_coverage_summary(db)

    @router.post(
        "/sync-date",
        response_model=IngestionSyncResponseSchema,
        summary="Verify or ingest a target delivery date from Elexon BMRS (WINDFOR + FUELHH) into PostgreSQL",
    )
    def post_sync_delivery_date(
        payload: IngestionSyncRequestSchema = Body(...),
        db: DatabaseConnection = Depends(get_db),
    ) -> Dict[str, Any]:
        try:
            return sync_delivery_date_from_elexon(
                db=db,
                delivery_date=payload.delivery_date,
                force_live_fetch=payload.force_live_fetch,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

except ImportError:
    router = None
