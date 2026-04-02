import asyncio
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException
from fastapi.responses import Response

from app import db
from app.core.session import SessionData, session_store
from app.core.websocket import ws_manager
from app.models import ReportCreateRequest, ReportListResponse, ReportResponse
from app.services import validation_service
from app.services.export_service import generate_csv_zip, generate_pdf

router = APIRouter()


def get_session(session_id: str = Cookie(default="")) -> SessionData:
    """FastAPI dependency: resolve session from httpOnly cookie."""
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return s


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
    session: SessionData = Depends(get_session),
):
    # Verify org access
    if request.org_id not in session.org_ids:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    job_id = str(uuid.uuid4())
    job = await db.create_job(
        job_id=job_id,
        mist_user_id=session.user_identifier,
        org_id=request.org_id,
        site_id=request.site_id,
        include_cable_tests=request.include_cable_tests,
    )

    background_tasks.add_task(
        validation_service.run_post_deployment_validation,
        job_id=job_id,
        site_id=request.site_id,
        cloud_region=session.mist_cloud,
        org_id=request.org_id,
        include_cable_tests=request.include_cable_tests,
        progress_callback=_progress_callback,
        token=session.mist_token,
        cookies=session.mist_cookies,
        csrftoken=session.mist_csrftoken,
    )

    return _job_to_response(job)


@router.get("/reports", response_model=ReportListResponse)
async def list_reports(session: SessionData = Depends(get_session)):
    jobs = await db.list_user_jobs(session.user_identifier)
    # Filter to only orgs the user currently has access to
    accessible = [j for j in jobs if j.get("org_id") in session.org_ids]
    return ReportListResponse(
        reports=[_job_to_response(j) for j in accessible],
        total=len(accessible),
    )


async def _get_authorized_job(job_id: str, session: SessionData) -> dict:
    """Fetch job and verify ownership + org access."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Report not found")
    if job["mist_user_id"] != session.user_identifier:
        raise HTTPException(status_code=403, detail="Access denied")
    if job["org_id"] not in session.org_ids:
        raise HTTPException(status_code=403, detail="Access denied to this organization")
    return job


def _safe_filename(site_name: str) -> str:
    return re.sub(r'[^\w\-.]', '_', site_name or "site")


@router.get("/reports/{job_id}", response_model=ReportResponse)
async def get_report(job_id: str, session: SessionData = Depends(get_session)):
    job = await _get_authorized_job(job_id, session)
    return _job_to_response(job)


@router.delete("/reports/{job_id}", status_code=204)
async def delete_report(job_id: str, session: SessionData = Depends(get_session)):
    await _get_authorized_job(job_id, session)
    await db.delete_job(job_id)
    return Response(status_code=204)


@router.get("/reports/{job_id}/export/pdf")
async def export_pdf(job_id: str, session: SessionData = Depends(get_session)):
    job = await _get_authorized_job(job_id, session)
    if job.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Report not completed")
    try:
        pdf_bytes = await asyncio.to_thread(generate_pdf, job)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="validation_{_safe_filename(job.get("site_name", "site"))}.pdf"'},
        )
    except Exception:
        raise HTTPException(status_code=500, detail="PDF generation failed")


@router.get("/reports/{job_id}/export/csv")
async def export_csv(job_id: str, session: SessionData = Depends(get_session)):
    job = await _get_authorized_job(job_id, session)
    if job.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Report not completed")
    try:
        zip_bytes = await asyncio.to_thread(generate_csv_zip, job)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="validation_{_safe_filename(job.get("site_name", "site"))}.zip"'},
        )
    except Exception:
        raise HTTPException(status_code=500, detail="CSV generation failed")
