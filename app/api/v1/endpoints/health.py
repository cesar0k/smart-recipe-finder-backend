from fastapi import APIRouter, Response, status

from app.core.health import HealthReport, is_embedding_model_ready, run_all_checks

router = APIRouter()


@router.get("/live", operation_id="health_live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    response_model=HealthReport,
    operation_id="health_ready",
)
async def health_ready(response: Response) -> HealthReport:
    report = await run_all_checks()
    if report.status == "fail":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report


@router.get("/startup", operation_id="health_startup")
async def health_startup(response: Response) -> dict[str, str]:
    if not is_embedding_model_ready():
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "starting"}
    return {"status": "ok"}
