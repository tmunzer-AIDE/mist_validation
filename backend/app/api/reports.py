import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from fastapi.responses import Response

from app import db
from app.core.websocket import ws_manager
from app.models import ReportCreateRequest, ReportListResponse, ReportResponse
from app.services import validation_service
from app.services.export_service import generate_csv_zip, generate_pdf

router = APIRouter()


def _job_to_response(job: dict) -> ReportResponse:
    progress = job.get("progress", {})
    if isinstance(progress, str):
        try:
            progress = json.loads(progress)
        except Exception:
            progress = {}

    result = job.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = None

    return ReportResponse(
        id=job["id"],
        mist_user_id=job["mist_user_id"],
        org_id=job["org_id"],
        site_id=job["site_id"],
        site_name=job.get("site_name", ""),
        status=job.get("status", "pending"),
        progress=progress,
        result=result,
        error=job.get("error"),
        include_cable_tests=bool(job.get("include_cable_tests", 0)),
        created_at=job.get("created_at", ""),
        completed_at=job.get("completed_at"),
    )


async def _progress_callback(job_id: str, payload: dict) -> None:
    # Update DB progress for report_progress type
    if payload.get("type") == "report_progress":
        data = payload.get("data", {})
        await db.update_job(job_id, progress=data)
    # Always broadcast to WebSocket
    await ws_manager.broadcast(f"report:{job_id}", payload)


@router.post("/reports", response_model=ReportResponse, status_code=201)
async def create_report(
    request: ReportCreateRequest,
    background_tasks: BackgroundTasks,
    x_mist_cloud: str = Header(...),
    x_mist_user_id: str = Header(...),
    x_mist_token: str | None = Header(default=None),
    x_mist_email: str | None = Header(default=None),
    x_mist_password: str | None = Header(default=None),
):
    job_id = str(uuid.uuid4())
    job = await db.create_job(
        job_id=job_id,
        mist_user_id=x_mist_user_id,
        org_id=request.org_id,
        site_id=request.site_id,
        include_cable_tests=request.include_cable_tests,
    )

    background_tasks.add_task(
        validation_service.run_post_deployment_validation,
        job_id=job_id,
        site_id=request.site_id,
        cloud_region=x_mist_cloud,
        org_id=request.org_id,
        include_cable_tests=request.include_cable_tests,
        progress_callback=_progress_callback,
        token=x_mist_token,
        email=x_mist_email,
        password=x_mist_password,
    )

    return _job_to_response(job)


@router.get("/reports", response_model=ReportListResponse)
async def list_reports(x_mist_user_id: str = Header(...)):
    jobs = await db.list_user_jobs(x_mist_user_id)
    return ReportListResponse(
        reports=[_job_to_response(j) for j in jobs],
        total=len(jobs),
    )


@router.get("/reports/{job_id}", response_model=ReportResponse)
async def get_report(job_id: str, x_mist_user_id: str = Header(...)):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Report not found")
    if job["mist_user_id"] != x_mist_user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return _job_to_response(job)


@router.get("/reports/{job_id}/export/pdf")
async def export_pdf(job_id: str, x_mist_user_id: str = Header(...)):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Report not found")
    if job["mist_user_id"] != x_mist_user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if job.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Report not completed")
    try:
        pdf_bytes = generate_pdf(job)
        site_name = job.get("site_name", "site").replace(" ", "_")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="validation_{site_name}.pdf"'},
        )
    except Exception:
        raise HTTPException(status_code=500, detail="PDF generation failed")


@router.get("/reports/{job_id}/export/csv")
async def export_csv(job_id: str, x_mist_user_id: str = Header(...)):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Report not found")
    if job["mist_user_id"] != x_mist_user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if job.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Report not completed")
    try:
        zip_bytes = generate_csv_zip(job)
        site_name = job.get("site_name", "site").replace(" ", "_")
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="validation_{site_name}.zip"'},
        )
    except Exception:
        raise HTTPException(status_code=500, detail="CSV generation failed")
