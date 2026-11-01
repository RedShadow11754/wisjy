"""
Cutoff Recommendation Policy Router (GET /api/v1/cutoffs/policy).
Returns the 8 Day-1 decision cutoffs (02:30 to 22:30 UTC) with December 2025
validation error curves and recommended execution tranches (07:30, 11:30, 15:30, 18:30).
"""

from typing import Any, Dict

from backend.config import PRIMARY_SWEET_SPOT_CUTOFFS_UTC, RECOMMENDED_CUTOFFS_UTC
from backend.services.cutoff_advisor import get_cutoff_policy_list


def build_cutoff_policy_payload() -> Dict[str, Any]:
    cutoffs = get_cutoff_policy_list()
    return {
        "total_daily_cutoffs": len(cutoffs),
        "recommended_cutoffs_utc": RECOMMENDED_CUTOFFS_UTC,
        "primary_sweet_spot_cutoffs_utc": PRIMARY_SWEET_SPOT_CUTOFFS_UTC,
        "calibration_source": "2025-12-01 to 2025-12-31 Validation Holdout Set (n=5,952 — Zero Jan 2026 Leakage)",
        "recommended_execution_strategy": {
            "tranche_1": "07:30 UTC (30% volume) — First major error drop (-118 MW vs 02:30) at morning market open.",
            "tranche_2": "11:30 UTC (35% volume) — Pre-auction lock before midday Day-Ahead clearing.",
            "tranche_3_sweet_spot": "15:30 / 18:30 UTC (35% volume) — Primary accuracy sweet spot (>50.7% error reduction vs Latest).",
        },
        "cutoffs": cutoffs,
    }


try:
    from fastapi import APIRouter
    from backend.schemas import CutoffPolicyResponseSchema

    router = APIRouter(prefix="/api/v1/cutoffs", tags=["Cutoff Policy & Advisor"])

    @router.get(
        "/policy",
        response_model=CutoffPolicyResponseSchema,
        summary="Retrieve all 8 Day-1 Elexon cutoff windows and recommended execution tranches",
    )
    def get_cutoff_policy() -> Dict[str, Any]:
        return build_cutoff_policy_payload()

except ImportError:
    router = None
