# Mist Post-Deployment Validation Report Generator

<img src="https://github.com/tmunzer/mist_validation/raw/main/._readme/img/banner.png" width="100%" />

A full-stack web application that validates Juniper Mist deployments (Access Points, Switches, and Gateways) and generates comprehensive PDF/CSV reports. Authenticate against the Mist API, run validation checks on sites, and get real-time progress updates via WebSocket.

## MIT LICENSE

Copyright (c) 2026 Thomas Munzer

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## Features

<img src="https://github.com/tmunzer/mist_validation/raw/main/._readme/img/report.png" width="60%" />

### Validation Checks
- **Template Variables** — Verifies all required template variables are defined
- **Configuration Events** — Reviews recent configuration change events
- **Device Events** — Analyzes device-level events and alerts
- **Access Points** — Validates AP health, firmware, connectivity status, and configuration
- **Switches** — Checks switch health, Virtual Chassis status, standalone and aggregated interfaces configurations and port status
- **Gateways** — Validates gateway health and configuration, WAN and LAN ports status, standalone and aggregated interfaces configurations and port status
- **Optic Modules** — Reports transceiver details (model, serial, part number) and validates Rx/Tx power levels for switches and gateways
- **Cable Tests** — Runs TDR cable diagnostics on switch ports (optional, requires write access and site group membership)

### Report Generation
- **PDF Reports** — Professional formatted reports with device details and status badges
- **CSV Export** — Structured data export for further analysis
- **Real-time Progress** — WebSocket-based live progress updates during validation

### Authentication
- Login with Mist credentials (username/password) or API token
- Two-factor authentication support
- Supports all Mist cloud regions (Global, EU, APAC, etc.)

## How It Works

1. **Authentication** — Log in with your Mist credentials or API token. The application validates your access rights against the Mist Cloud.

2. **Site Selection** — Select your organization and the site(s) you want to validate. The application retrieves the site configuration and device inventory.

3. **Validation** — The application runs comprehensive validation checks on all devices and configurations. Progress is streamed in real-time via WebSocket.

4. **Report Generation** — Once complete, export your validation report as a PDF or CSV file.

> **Note**: No credentials are stored on the server. Authentication headers are passed from the browser on each request, and all session data is cached client-side. Reports are automatically deleted after 24 hours.

## Installation

This application can be run as a standalone Python/Angular application or deployed as a Docker container.

> **Note**: The application does not provide HTTPS encryption. It is highly recommended to deploy it behind a reverse proxy (nginx, Caddy, etc.) that provides HTTPS.

### Docker Deployment (Recommended)

The Docker image is available on Docker Hub: [tmunzer/mist_validation](https://hub.docker.com/r/tmunzer/mist_validation)

```bash
docker pull tmunzer/mist_validation
docker run -d -p 8080:8080 -v /data:/data tmunzer/mist_validation
```

Or build locally:

```bash
# Build frontend first
cd frontend && npm install && npm run build && cd ..
make angular  # Copies frontend to backend static directory

# Build Docker image
docker build -t mist-validation .
docker run -d -p 8080:8080 -v /data:/data mist-validation
```

The container exposes port **8080** and stores the SQLite database at `/data/reports.db`. Mount `/data` as a volume for persistence.

### Standalone Deployment

#### Prerequisites
- Python 3.12+
- Node.js 18+

#### Backend Setup
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

#### Frontend Setup (Development)
```bash
cd frontend
npm install
npm start  # Dev server on :4200, proxies API requests to :8080
```

#### Production Build
```bash
make angular  # Builds frontend and copies to backend/app/static
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Configuration

### Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATABASE_PATH` | String | `reports.db` | Path to SQLite database file |
| `TDR_SITE_GROUP` | String | `tdr_validation` | Site group name for cable test eligibility. Set empty to disable group gating. |

### Docker Compose Example

```yaml
version: '3.8'
services:
  mist-validation:
    image: tmunzer/mist_validation
    ports:
      - "8080:8080"
    volumes:
      - mist_data:/data
    restart: unless-stopped

volumes:
  mist_data:
```

## Validation Thresholds

### Optic Module Power Levels

The following thresholds are applied to Rx and Tx optical power readings (in dBm) from SFP/SFP+ transceivers. These are conservative values suitable for most enterprise-grade optics.

| Metric | Pass | Warning | Fail |
|--------|------|---------|------|
| Rx Power | >= -20 dBm | -25 to -20 dBm | < -25 dBm |
| Tx Power | >= -8 dBm | -12 to -8 dBm | < -12 dBm |

Thresholds are displayed in the web UI (device detail dialog), in the PDF report (below each optics table), and in the CSV export (`rx_power_status` / `tx_power_status` columns).

### Cable Test Prerequisites

Cable tests (TDR diagnostics) send commands to live switches. Two safety layers prevent accidental use on production sites:

1. **Write access required** — The user must have `admin` or `write` role for the organization. Read-only users cannot trigger cable tests.

2. **Site group membership** — The site must belong to a configurable site group (default: `tdr_validation`). This requires an administrator to:
   - Create a site group named `tdr_validation` (or the value of `TDR_SITE_GROUP` env var) in the Mist dashboard
   - Add the sites where cable tests are authorized to this group

Set `TDR_SITE_GROUP` to an empty string to disable site group gating (cable tests will only require write access).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (Angular 21)                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │    Login    │  │Site Selector│  │      Report View        │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│                              │                                   │
│              HTTP + WebSocket (X-Mist-* headers)                 │
└──────────────────────────────┼───────────────────────────────────┘
                               │
┌──────────────────────────────┼───────────────────────────────────┐
│                        Backend (FastAPI)                         │
│  ┌─────────┐  ┌──────────────┐  ┌────────────────────────────┐  │
│  │  Auth   │  │  Validation  │  │      Export (PDF/CSV)      │  │
│  └─────────┘  └──────────────┘  └────────────────────────────┘  │
│                      │                                           │
│              mistapi library                                     │
└──────────────────────┼───────────────────────────────────────────┘
                       │
              Mist Cloud API
```

## Screenshots

*Screenshots will be added here*

## Support

This is a community project and is not officially supported by Juniper Networks. For issues and feature requests, please open a GitHub issue.
