import os
import time

from fastapi import APIRouter, Header, HTTPException

from app import db
from app.core.session import session_store

router = APIRouter()

STATUS_TOKEN = os.environ.get("STATUS_TOKEN", "")

_start_time = time.time()


def _check_auth(authorization: str | None) -> None:
    if not STATUS_TOKEN:
        raise HTTPException(status_code=404)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    if authorization[7:] != STATUS_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.get("/status")
async def get_status(authorization: str | None = Header(default=None)):
    _check_auth(authorization)

    # DB health check
    db_status = "ok"
    report_count = 0
    try:
        report_count = await db.get_report_count()
    except Exception:
        db_status = "error"

    overall = "ok" if db_status == "ok" else "error"

    return {
        "status": overall,
        "uptime_seconds": int(time.time() - _start_time),
        "database": {
            "status": db_status,
            "report_count": report_count,
        },
        "sessions": {
            "active": session_store.count(),
        },
    }
