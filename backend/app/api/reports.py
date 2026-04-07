import asyncio
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response

from app import db
from app.api.deps import get_session
from app.api.sites import TDR_SITE_GROUP
from app.core.session import SessionData
from app.core.websocket import ws_manager
from app.models import ReportCreateRequest, ReportListResponse, ReportResponse
from app.services import validation_service
from app.services.export_service import generate_csv_zip, generate_pdf
from app.services.mist_service import MistService

router = APIRouter()


def _resolve_org_name(session: SessionData, org_id: str) -> str:
    for priv in session.privileges:
        if isinstance(priv, dict) and priv.get("scope") == "org" and priv.get("org_id") == org_id:
            return priv.get("name", "") or priv.get("org_name", "") or org_id[:8]
    return org_id[:8]


def _job_to_response(job: dict) -> ReportResponse:
    # progress and result are already deserialized by db._deserialize_row
    return ReportResponse(
        id=job["id"],
        org_id=job["org_id"],
        org_name=job.get("org_name", ""),
        site_id=job["site_id"],
        site_name=job.get("site_name", ""),
        status=job.get("status", "pending"),
        progress=job.get("progress", {}),
        result=job.get("result"),
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
    org_id = str(request.org_id)
    site_id = str(request.site_id)

    # Verify org access
    if org_id not in session.org_ids:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    # Cable test safety checks
    if request.include_cable_tests:
        if not session.can_write(org_id):
            raise HTTPException(
                status_code=403,
                detail="Cable tests require write access to the organization.",
            )
        if TDR_SITE_GROUP:
            mist = MistService(
                org_id=org_id,
                cloud_region=session.mist_cloud,
                api_token=session.mist_token,
                cookies=session.mist_cookies,
                csrftoken=session.mist_csrftoken,
            )
            site_groups = await mist.get_site_groups()
            tdr_group = next(
                (g for g in site_groups if g.get("name") == TDR_SITE_GROUP),
                None,
            )
            if not tdr_group:
                raise HTTPException(
                    status_code=403,
                    detail=f"Cable tests are not available. The site group '{TDR_SITE_GROUP}' does not exist.",
                )
            # Check site membership via site's sitegroup_ids (same as sites.py)
            site_data = await mist.get_site(site_id)
            if tdr_group["id"] not in site_data.get("sitegroup_ids", []):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cable tests are not enabled for this site. Add it to the '{TDR_SITE_GROUP}' site group.",
                )

    job_id = str(uuid.uuid4())
    org_name = _resolve_org_name(session, org_id)
    job = await db.create_job(
        job_id=job_id,
        mist_user_id=session.user_identifier,
        org_id=org_id,
        org_name=org_name,
        site_id=site_id,
        include_cable_tests=request.include_cable_tests,
    )

    background_tasks.add_task(
        validation_service.run_post_deployment_validation,
        job_id=job_id,
        site_id=site_id,
        cloud_region=session.mist_cloud,
        org_id=org_id,
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
