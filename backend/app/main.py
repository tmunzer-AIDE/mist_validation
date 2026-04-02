import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import auth, reports, sites, status, ws
from app.core.session import session_store
from app.core.websocket import ws_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    scheduler.add_job(db.cleanup_old_jobs, "interval", hours=1, id="cleanup_old_jobs")
    scheduler.add_job(session_store.cleanup_expired, "interval", hours=1, id="cleanup_sessions")
    scheduler.start()
    logger.info("Startup complete")
    yield
    ws_manager.stop_heartbeat()
    scheduler.shutdown()
    logger.info("Shutdown complete")


app = FastAPI(title="Mist Post-Validation Report", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(auth.router, prefix="/api")
app.include_router(sites.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(status.router, prefix="/api")
app.include_router(ws.router)

# Serve Angular frontend (built files in app/frontend/)
static_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
