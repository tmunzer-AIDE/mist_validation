# Copilot Instructions

## Project Overview

Mist Post-Deployment Validation Report Generator — a full-stack web app that validates Juniper Mist wireless network deployments. It authenticates against the Mist API, runs validation checks on sites (APs, switches, gateways, WLANs, template variables, cable tests, device events), and generates PDF/CSV reports. Real-time progress is streamed via WebSocket.

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

### Full build (copies frontend into backend static dir)

```bash
make angular       # Builds frontend with --deploy-url static/, copies to backend/app/frontend/
```

### Docker

```bash
docker build -t mist-validation .
docker run -p 8080:8080 -v /data:/data mist-validation
```

The frontend must be pre-built (via `make angular`) before building the Docker image.

### Tests

```bash
cd backend
python -m pytest tests/                          # Run all tests
python -m pytest tests/test_merge_port_configs.py  # Run a single test file
python -m pytest tests/test_merge_port_configs.py::TestMergePortConfigs::test_empty_configs  # Single test
```

Tests use plain pytest with class-based grouping. No test suite exists for the frontend. No linter configuration exists for the backend.

### Frontend Formatting

Prettier is configured in `frontend/package.json`: 100-char width, single quotes, `angular` HTML parser.

## Architecture

**Backend** (`backend/app/`) — FastAPI (Python 3.12), async throughout:

- `main.py` — App entrypoint with lifespan (DB init, APScheduler for hourly cleanup of old jobs and expired sessions), mounts API routers and serves the Angular frontend as static files.
- `api/` — Route handlers: `auth.py` (login/logout/session), `sites.py` (list sites), `reports.py` (create/list/delete/export reports), `ws.py` (WebSocket endpoint), `deps.py` (shared FastAPI dependencies).
- `services/` — Business logic: `mist_service.py` (Mist API wrapper using `mistapi` library, supports multiple cloud regions), `validation_service.py` (runs all validation checks per device type), `export_service.py` (PDF via ReportLab, CSV generation).
- `db.py` — aiosqlite persistence for report jobs. Results stored as JSON blobs. Reports auto-deleted after 24h.
- `core/websocket.py` — Channel-based WebSocket pub/sub (`report:{job_id}` pattern) with heartbeat. Module-level `ws_manager` singleton.
- `core/session.py` — In-memory server-side session store with 24h TTL. Module-level `session_store` singleton.
- `models.py` — Pydantic request/response models.
- `utils/` — `variables.py` (expected template variable definitions), `cable_test.py` (ANSI output parser), `event_definitions.py` (trigger/clear event pair definitions).

**Frontend** (`frontend/src/app/`) — Angular 21, zoneless change detection, standalone components:

- `core/services/` — `api.service.ts` (HTTP client, all requests use `withCredentials: true`), `ws.service.ts` (WebSocket client with reconnection).
- `features/` — `login/`, `site-selector/`, `report-view/`, `validation-reference/`.
- `shared/components/` — Reusable UI components.
- Uses Angular Material for UI, Angular signals for state management, no NgModules.

### Auth Flow

Login authenticates against Mist Cloud and creates a server-side session. An httpOnly cookie (`session_id`) is returned to the browser. All subsequent API requests carry this cookie (`withCredentials: true`). The `get_session` dependency in `api/deps.py` resolves the session from the cookie or returns 401. No credentials are stored client-side beyond the session cookie.

### Report Flow

`POST /api/reports` → creates DB job + starts background validation task → progress broadcast on WebSocket channel `report:{job_id}` → client listens for completion → export via `/api/reports/{id}/pdf` or `/api/reports/{id}/csv`.

## Key Conventions

- Backend uses app-relative imports: `from app.models import ...`, `from app.services.mist_service import ...`
- All backend route handlers are async
- Backend singletons (`ws_manager`, `session_store`) are module-level instances imported directly
- Frontend uses `inject()` function for dependency injection (not constructor injection)
- Frontend uses Angular signals (not RxJS BehaviorSubjects) for component state
- All Angular components are standalone (no NgModules)
- ENV vars: `DATABASE_PATH` (SQLite location, defaults to `/data/reports.db` or `./reports.db`), `TDR_SITE_GROUP` (site group name for cable test eligibility)
