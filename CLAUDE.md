# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mist Post-Deployment Validation Report Generator — a full-stack web app that validates Juniper Mist wireless network deployments. It authenticates against the Mist API, runs validation checks on sites (APs, switches, gateways, WLANs, template variables, cable tests), and generates PDF/CSV reports. Real-time progress is streamed via WebSocket.

## Development Commands

### Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### Frontend
```bash
cd frontend
npm install
npm start          # Dev server on :4200, proxies /api→:8080 and /ws→:8080
npm run build      # Production build to dist/browser/
```

### Local development build (copies frontend into backend static dir)
```bash
./angular-buid.sh  # Builds frontend with --deploy-url static/, copies to backend/app/static
```

### Docker
```bash
docker build -t mist-validation .
docker run -p 8080:8080 -v /data:/data mist-validation
```

The Dockerfile uses Python 3.12-slim to run the FastAPI backend. The frontend must be pre-built locally (via `./angular-buid.sh` or `npm run build`) before building the Docker image. SQLite database lives at `/data/reports.db` (mount as volume).

## Architecture

**Backend** (`backend/app/`) — FastAPI (Python 3.12), async throughout:
- `main.py` — App entrypoint, lifespan (DB init, APScheduler for hourly job cleanup), mounts routers and static files
- `api/` — Route handlers: `auth.py` (login via token or credentials), `sites.py` (list sites), `reports.py` (create/list/export reports), `ws.py` (WebSocket endpoint)
- `services/` — Business logic: `mist_service.py` (Mist API wrapper using `mistapi` library, supports multiple cloud regions), `validation_service.py` (runs all validation checks), `export_service.py` (PDF via ReportLab, CSV generation)
- `db.py` — aiosqlite persistence for report jobs (results stored as JSON blobs)
- `core/websocket.py` — Channel-based pub/sub (`report:{job_id}` pattern) with heartbeat
- `models.py` — Pydantic request/response models
- `utils/variables.py` — Expected template variable definitions, `cable_test.py` — ANSI output parser

**Frontend** (`frontend/src/app/`) — Angular 21, zoneless change detection, standalone components:
- `core/services/` — `api.service.ts` (HTTP client), `ws.service.ts` (WebSocket client with reconnection)
- `features/` — `login/` (auth form), `site-selector/` (org/site picker), `report-view/` (results display with device detail dialogs)
- `shared/components/` — Reusable UI (e.g., `status-badge`)
- Uses Angular Material for UI components, Angular signals for state

**Auth flow**: Mist credentials are passed from frontend to backend via `X-Mist-*` custom headers on every request — no server-side session.

**Report flow**: POST to `/api/reports` starts a background validation task → progress broadcast on WebSocket channel `report:{job_id}` → client polls or listens for completion → export via `/api/reports/{id}/pdf` or `/api/reports/{id}/csv`.

## Key Conventions

- Backend uses relative imports (`from app.models import ...`)
- Frontend uses Prettier (100 char width, single quotes, angular HTML parser)
- No test suite exists currently
- No linter configuration for the backend
- ENV var `DATABASE_PATH` controls SQLite location (defaults to `reports.db` in working dir)
