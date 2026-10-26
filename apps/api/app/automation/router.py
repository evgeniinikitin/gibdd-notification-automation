"""Small framework adapter for automation health and safe preflight checks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .exceptions import PayloadValidationError
from .payload import PayloadBuilderService
from .schemas import GibddPayloadDTO
from .submission import SubmissionWorkerConfig


router = APIRouter(prefix="/automation", tags=["automation"])


@router.get("/health")
async def automation_health() -> dict[str, Any]:
    config = SubmissionWorkerConfig.from_env()
    return {
        "status": "ok",
        "service": "buscript-automation",
        "live_submission": config.live_submission,
        "headful": config.headful,
        "dry_run_safe": not config.live_submission,
    }


@router.get("/payload-schema")
async def payload_schema() -> dict[str, Any]:
    return PayloadBuilderService.json_schema()


@router.post("/dry-run")
async def dry_run_preflight(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the browser contract without opening a browser or sending data."""

    try:
        validated = PayloadBuilderService().validate(payload)
    except PayloadValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "validated",
        "tenant_id": validated.tenant_id,
        "carrier_key": validated.carrier_key,
        "children_count": validated.transportation.children_count,
        "vehicle_count": len(validated.vehicles),
        "driver_count": len(validated.drivers),
        "file_count": len(validated.files),
        "would_final_submit": False,
        "message": (
            "Payload прошел preflight. Этот endpoint никогда не открывает "
            "портал и не выполняет финальную отправку."
        ),
    }


@router.post("/validate", response_model=GibddPayloadDTO)
async def validate_payload(payload: dict[str, Any]) -> GibddPayloadDTO:
    try:
        return PayloadBuilderService().validate(payload)
    except PayloadValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
