# Marvis Minis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Juniper Mist Marvis Minis as a new opt-in synthetic-test step inside the post-deployment validation flow, with a top-level result section, an AP × VLAN matrix UI, live WebSocket progress, and PDF/CSV exports.

**Architecture:** New `backend/app/services/marvis_service.py` owns trigger + poll loop + parser + scorer + WS broadcasts. `validation_service.py` adds it as the last step in `_STEPS`, conditional on the new `include_marvis_minis` flag. Frontend gets a new opt-in card on the site selector, a new top-level matrix component on the report view that reads from a live WS signal during the test and from the persisted result after, plus PDF/CSV export rows.

**Tech Stack:** Python 3.12, FastAPI, `mistapi` (using `session.mist_post`/`session.mist_get` for the `/api/v1/labs/...` endpoint not in the SDK), aiosqlite, ReportLab. Angular 21 (standalone, signals, zoneless), Angular Material.

**Source spec:** `docs/superpowers/specs/2026-05-01-marvis-minis-design.md`.

---

## Project conventions for this plan

This codebase has **no test infrastructure** (per `CLAUDE.md`). Adding pytest fixtures + structure for one feature would be inconsistent with the rest of the code, so this plan follows the project's manual-verification model:

- Each task ends with **manual verification steps** (run the dev server, exercise the feature, observe expected output) instead of failing-test-first cycles.
- Type checking uses Python's standard library only — `python -c "import app.services.marvis_service"` smoke-tests imports.
- Frontend uses `ng build` for type checking (no `ng test`).
- Backend changes that are pure functions get an inline `python -c` smoke check using a hand-typed sample payload (the in-progress payload from the spec).

---

## File structure

**Create**
- `backend/app/services/marvis_service.py` — trigger, poll loop, parser, scorer, WS broadcaster.
- `frontend/src/app/features/report-view/marvis-matrix.component.ts` — AP × VLAN grid.
- `frontend/src/app/features/report-view/marvis-matrix.component.html`
- `frontend/src/app/features/report-view/marvis-matrix.component.scss`
- `frontend/src/app/features/report-view/marvis-detail-dialog.component.ts` — per-cell drill-down dialog.
- `frontend/src/app/features/report-view/marvis-detail-dialog.component.html`
- `frontend/src/app/features/report-view/marvis-detail-dialog.component.scss`

**Modify (with the line ranges that exist today; line numbers will shift as edits land)**
- `backend/app/db.py` — schema migration + `create_job` flag.
- `backend/app/models.py:19-30` — `ReportCreateRequest.include_marvis_minis`; add to `ReportResponse`.
- `backend/app/api/reports.py:28-46, 89-167` — write-access gate, pass flag through.
- `backend/app/services/validation_service.py:85-96` (`_STEPS`), `:99-240` (`_ProgressTracker`), `:243-563` (orchestrator), `:2346-2377` (`_compute_summary`).
- `backend/app/services/export_service.py` — PDF section + CSV file.
- `frontend/src/app/features/site-selector/site-selector.component.ts` — toggle ctrl + body submission.
- `frontend/src/app/features/site-selector/site-selector.component.html` — opt-in card.
- `frontend/src/app/features/running-screen/running-screen.component.ts` — `PHASE_BY_STEP`.
- `frontend/src/app/features/report-view/report-view.component.ts` — WS handler branch, live signal, dialog wiring.
- `frontend/src/app/features/report-view/report-view.component.html` — top-level section render.
- `frontend/src/app/features/validation-reference/validation-reference.component.ts` — new entry.

---

## Task 1 — Backend: request flag, DB migration, API gate

**Goal:** Plumb the new `include_marvis_minis` flag from the HTTP request through to the orchestrator, with a write-access gate and a SQLite migration.

**Files:**
- Modify: `backend/app/models.py:19-63`
- Modify: `backend/app/db.py:18-127`
- Modify: `backend/app/api/reports.py:28-46, 89-167`

- [ ] **Step 1.1: Add `include_marvis_minis` to `ReportCreateRequest` and `ReportResponse`**

In `backend/app/models.py`, change:

```python
class ReportCreateRequest(BaseModel):
    site_id: UUID | None = None
    org_id: UUID
    scope: Literal["site", "org"] = "site"
    include_cable_tests: bool = False
    include_config_errors: bool = False
```

to:

```python
class ReportCreateRequest(BaseModel):
    site_id: UUID | None = None
    org_id: UUID
    scope: Literal["site", "org"] = "site"
    include_cable_tests: bool = False
    include_config_errors: bool = False
    include_marvis_minis: bool = False
```

And in `ReportResponse` (around line 44-58), change:

```python
    include_cable_tests: bool
    include_config_errors: bool
```

to:

```python
    include_cable_tests: bool
    include_config_errors: bool
    include_marvis_minis: bool
```

- [ ] **Step 1.2: Add SQLite column + migration in `backend/app/db.py`**

In `_CREATE_TABLE_SQL` (line 18-37), change:

```sql
    include_cable_tests INTEGER DEFAULT 0,
    include_config_errors INTEGER DEFAULT 0,
```

to:

```sql
    include_cable_tests INTEGER DEFAULT 0,
    include_config_errors INTEGER DEFAULT 0,
    include_marvis_minis INTEGER DEFAULT 0,
```

In `init_db` (after the `scope` migration block, around line 62), append:

```python
        # Migration: add include_marvis_minis column to existing databases
        try:
            await db.execute("ALTER TABLE reports ADD COLUMN include_marvis_minis INTEGER DEFAULT 0")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # Column already exists
```

In `create_job` signature (line 90-99), add the parameter:

```python
async def create_job(
    job_id: str,
    mist_user_id: str,
    org_id: str,
    site_id: str,
    org_name: str = "",
    include_cable_tests: bool = False,
    include_config_errors: bool = False,
    include_marvis_minis: bool = False,
    scope: str = "site",
) -> dict:
```

In the `INSERT INTO reports (...)` SQL inside `create_job`, change to include the new column:

```python
        await db.execute(
            """
            INSERT INTO reports (id, mist_user_id, org_id, org_name, site_id, status, progress, include_cable_tests, include_config_errors, include_marvis_minis, scope, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', '{}', ?, ?, ?, ?, ?)
            """,
            (job_id, mist_user_id, org_id, org_name, site_id, int(include_cable_tests), int(include_config_errors), int(include_marvis_minis), scope, now),
        )
```

In the dict returned by `create_job`, add the field:

```python
        "include_marvis_minis": int(include_marvis_minis),
```

- [ ] **Step 1.3: Wire flag through `api/reports.py`**

In `_job_to_response` (line 28-45), add the field to the returned `ReportResponse`:

```python
        include_cable_tests=bool(job.get("include_cable_tests", 0)),
        include_config_errors=bool(job.get("include_config_errors", 0)),
        include_marvis_minis=bool(job.get("include_marvis_minis", 0)),
```

In `check_budget` query parameters (line 67-86), no change needed for v1 — Marvis runs at fixed wall-clock cost; we don't bill it against the API budget.

In `create_report` (line 89-169), after the existing cable-tests safety block (around line 102-127), add a new safety block for Marvis Minis (still inside `if request.scope == "site":`):

```python
        # Marvis Minis safety check
        if request.include_marvis_minis:
            if not session.can_write(org_id):
                raise HTTPException(
                    status_code=403,
                    detail="Marvis Minis requires write access to the organization.",
                )
```

In the `db.create_job(...)` call (line 131-140), add the field:

```python
    job = await db.create_job(
        job_id=job_id,
        mist_user_id=session.user_identifier,
        org_id=org_id,
        org_name=org_name,
        site_id=site_id,
        include_cable_tests=request.include_cable_tests if request.scope == "site" else False,
        include_config_errors=request.include_config_errors,
        include_marvis_minis=request.include_marvis_minis if request.scope == "site" else False,
        scope=request.scope,
    )
```

In the site-scope `background_tasks.add_task(...)` invocation (line 155-167), add:

```python
        background_tasks.add_task(
            validation_service.run_post_deployment_validation,
            job_id=job_id,
            site_id=site_id,
            cloud_region=session.mist_cloud,
            org_id=org_id,
            include_cable_tests=request.include_cable_tests,
            include_config_errors=request.include_config_errors,
            include_marvis_minis=request.include_marvis_minis,
            progress_callback=_progress_callback,
            token=session.mist_token,
            cookies=session.mist_cookies,
            csrftoken=session.mist_csrftoken,
        )
```

(We're calling `run_post_deployment_validation` with a parameter that doesn't exist yet — that's added in Task 4. The orchestrator will fail to start if Task 4 isn't done. That's intentional: it's the smallest unit of independently committable work.)

- [ ] **Step 1.4: Manual verification — backend starts, schema applies**

Stop the dev backend if running. Delete `./reports.db` (or wherever `DATABASE_PATH` points) for a clean migration test:

```bash
cd /Users/tmunzer/4_dev/mist_validation/backend
rm -f reports.db
```

Then check that imports still work and the schema is valid:

```bash
.venv/bin/python -c "import asyncio; from app.db import init_db; asyncio.run(init_db()); print('ok')"
.venv/bin/python -c "import sqlite3; cols = [r[1] for r in sqlite3.connect('reports.db').execute('pragma table_info(reports)').fetchall()]; print('include_marvis_minis' in cols)"
```

Expected output: `ok` then `True`.

Now drop the column to simulate an upgrade-in-place migration path:

```bash
.venv/bin/python -c "import sqlite3; sqlite3.connect('reports.db').execute('CREATE TABLE reports2 AS SELECT id, mist_user_id, org_id, org_name, site_id, site_name, status, progress, result, error, include_cable_tests, include_config_errors, scope, created_at, completed_at FROM reports'); "
.venv/bin/python -c "import sqlite3; c = sqlite3.connect('reports.db'); c.execute('DROP TABLE reports'); c.execute('ALTER TABLE reports2 RENAME TO reports'); c.commit()"
.venv/bin/python -c "import sqlite3; cols = [r[1] for r in sqlite3.connect('reports.db').execute('pragma table_info(reports)').fetchall()]; print('include_marvis_minis' in cols, 'before')"
.venv/bin/python -c "import asyncio; from app.db import init_db; asyncio.run(init_db()); import sqlite3; cols = [r[1] for r in sqlite3.connect('reports.db').execute('pragma table_info(reports)').fetchall()]; print('include_marvis_minis' in cols, 'after')"
```

Expected: `False before`, `True after`.

- [ ] **Step 1.5: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add backend/app/models.py backend/app/db.py backend/app/api/reports.py
git commit -m "feat(marvis): add include_marvis_minis flag, DB migration, write-access gate"
```

---

## Task 2 — Backend: pure parser/scorer in `marvis_service.py`

**Goal:** Build the pure-function parts of the Marvis service first — input is the raw `test_details` payload, output is the schema defined in §5 of the spec. No I/O. Easy to smoke-test.

**Files:**
- Create: `backend/app/services/marvis_service.py`

- [ ] **Step 2.1: Create the file with parser + scorer**

Write the file `backend/app/services/marvis_service.py`:

```python
"""
Marvis Minis synthetic-test service.

Public entry point: `run_marvis_minis`. Trigger and poll loop are added in a
later task; this module currently exposes the pure parsing + scoring helpers
that convert a raw `test_details` API response into the report schema described
in docs/superpowers/specs/2026-05-01-marvis-minis-design.md §5.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Status mapping ────────────────────────────────────────────────────────

# Maps Mist's raw test_status values onto the report schema status values.
# `not_validated` → `info` (and excluded from score). Anything else → `info`
# defensively so unexpected statuses don't crash the parser.
_TEST_STATUS_MAP: dict[str, str] = {
    "success": "pass",
    "failure": "fail",
    "not_validated": "info",
}

# vlan-level rollup uses this ordering; higher index = worse.
_STATUS_RANK: dict[str, int] = {
    "not_tested": 0,
    "info": 1,
    "pass": 2,
    "warn": 3,
    "fail": 4,
}


def _map_status(raw_status: str | None) -> str:
    return _TEST_STATUS_MAP.get(raw_status or "", "info")


# ── Per-test summary string ───────────────────────────────────────────────

def _summarize_dhcp(detail: dict, status: str) -> str:
    d = detail.get("dhcpv4") or {}
    if status == "pass":
        ip = d.get("ip", "")
        ack = d.get("ack_latency")
        return f"{ip} · {ack}ms" if ip else "DHCP complete"
    state = d.get("state") or "DHCP failed"
    summary = d.get("summary") or ""
    if "Retry count" in summary:
        # "DHCP DISCOVER Sent. Retry count [4]" → "DHCP Unresponsive · retry 4"
        try:
            n = summary.split("[", 1)[1].split("]", 1)[0]
            return f"{state} · retry {n}"
        except Exception:
            return state
    return state


def _summarize_arp(detail: dict, status: str) -> str:
    ips = detail.get("ips") or []
    if status == "pass" and ips:
        first = ips[0]
        return f"{first.get('ip', '')} · {first.get('latency', '?')}ms"
    return "ARP failed"


def _summarize_dns(detail: dict, status: str) -> str:
    urls = detail.get("urls") or []
    if not urls:
        return "DNS no URLs"
    failed = sum(1 for u in urls if "error" in u or not u.get("ips"))
    if failed:
        return f"{failed}/{len(urls)} URLs failed"
    latencies = [u.get("latency") for u in urls if isinstance(u.get("latency"), (int, float))]
    avg = round(sum(latencies) / len(latencies)) if latencies else None
    return f"{len(urls)} URLs · avg {avg}ms" if avg is not None else f"{len(urls)} URLs"


def _summarize_curl(detail: dict, status: str) -> str:
    urls = detail.get("urls") or []
    if not urls:
        return "CURL no URLs"
    ok = [u for u in urls if u.get("response")]
    failed = [u for u in urls if "error" in u]
    if status == "fail" and failed:
        # Trim to first error type for readability
        first_err = (failed[0].get("error") or "").split(":", 1)[0]
        return f"{len(failed)}/{len(urls)} failed · {first_err}".strip(" ·")
    latencies = [u.get("latency") for u in ok if isinstance(u.get("latency"), (int, float))]
    avg = round(sum(latencies) / len(latencies)) if latencies else None
    return f"{len(ok)}/{len(urls)} · avg {avg}ms" if avg is not None else f"{len(ok)}/{len(urls)}"


def _summarize_test(test_type: str, detail: dict, status: str) -> str:
    if status == "info":
        return "Not validated"
    fn = {
        "DHCP": _summarize_dhcp,
        "ARP": _summarize_arp,
        "DNS": _summarize_dns,
        "CURL": _summarize_curl,
    }.get(test_type)
    return fn(detail or {}, status) if fn else (detail.get("summary") or "")


# ── VLAN/AP rollup ────────────────────────────────────────────────────────

def _rollup_vlan_status(test_statuses: list[str]) -> str:
    """Worst of the VLAN's test statuses, ranked by _STATUS_RANK."""
    if not test_statuses:
        return "not_tested"
    return max(test_statuses, key=lambda s: _STATUS_RANK.get(s, 0))


def _parse_vlan(raw_vlan: dict) -> dict:
    """Convert one raw vlan entry into the schema's vlan block."""
    connectivity = raw_vlan.get("connectivity") or []
    has_pcap = bool(raw_vlan.get("has_pcap"))
    pcap_url = raw_vlan.get("pcap_url")

    if not connectivity:
        return {
            "vlan": raw_vlan.get("vlan"),
            "status": "not_tested",
            "has_pcap": has_pcap,
            "pcap_url": pcap_url,
            "tests": [],
        }

    tests: list[dict] = []
    for conn in connectivity:
        test_type = conn.get("test_type", "")
        raw_status = conn.get("test_status")
        status = _map_status(raw_status)
        detail = conn.get("test_detail") or {}
        tests.append({
            "test_type": test_type,
            "status": status,
            "summary": _summarize_test(test_type, detail, status),
            "detail": detail,
        })

    vlan_status = _rollup_vlan_status([t["status"] for t in tests])
    return {
        "vlan": raw_vlan.get("vlan"),
        "status": vlan_status,
        "has_pcap": has_pcap,
        "pcap_url": pcap_url,
        "tests": tests,
    }


def _parse_ap(raw_ap: dict) -> dict:
    """Convert one raw test_details entry into the schema's ap_results block."""
    return {
        "ap_mac": raw_ap.get("ap_mac", ""),
        "ap_name": raw_ap.get("ap_name", ""),
        "switch_name": raw_ap.get("switch_name", ""),
        "switch_port": raw_ap.get("port_id", "") or raw_ap.get("port_desc", ""),
        "vlans": [_parse_vlan(v) for v in (raw_ap.get("vlans") or [])],
    }


def parse_test_details(raw: dict) -> list[dict]:
    """Convert the API's `data.test_details` array into the schema's ap_results."""
    test_details = raw.get("test_details") or []
    return [_parse_ap(ap) for ap in test_details]


# ── Score impact ──────────────────────────────────────────────────────────

def compute_marvis_summary(ap_results: list[dict]) -> dict:
    """Sum every per-test status across all APs/VLANs into pass/fail/warn counts.

    `info` (not_validated) and vlan-level `not_tested` are excluded.
    """
    counts = {"pass": 0, "fail": 0, "warn": 0}
    for ap in ap_results:
        for vlan in ap.get("vlans", []):
            for test in vlan.get("tests", []):
                s = test.get("status")
                if s in counts:
                    counts[s] += 1
    return counts


# ── ap_count derivation (helper for live progress) ────────────────────────

def derive_ap_counts(raw: dict) -> tuple[int, int]:
    """Return (done_aps, total_aps) from a snapshot. `data.ap_count` is sometimes
    a number (in-progress) and sometimes a "N/M" string (final)."""
    data = raw.get("data") or raw
    ap_count = data.get("ap_count")
    test_details = data.get("test_details") or []
    total = len(test_details)
    done = sum(1 for ap in test_details if ap.get("end") is not None)
    if isinstance(ap_count, str) and "/" in ap_count:
        try:
            done_str, total_str = ap_count.split("/", 1)
            return int(done_str), int(total_str)
        except ValueError:
            pass
    elif isinstance(ap_count, int):
        total = max(total, ap_count)
    return done, total
```

- [ ] **Step 2.2: Smoke-test the parser**

Save the spec's in-progress example payload to a temp file and run the parser against it:

```bash
cd /Users/tmunzer/4_dev/mist_validation/backend
.venv/bin/python <<'PY'
from app.services.marvis_service import parse_test_details, compute_marvis_summary, derive_ap_counts

raw = {
    "test_details": [
        {
            "ap_mac": "04a92439fb75",
            "ap_name": "DNT-NTR-APB",
            "end": 1777569985,
            "port_id": "mge-0/0/0",
            "switch_name": "DNT-NTR-SWB-3",
            "vlans": [
                {
                    "vlan": 7, "has_pcap": False,
                    "connectivity": [
                        {"test_type": "DHCP", "test_status": "success", "test_detail": {"dhcpv4": {"ack_latency": 1, "ip": "10.3.7.18/24", "state": "DHCP Complete"}}},
                        {"test_type": "ARP", "test_status": "success", "test_detail": {"ips": [{"ip": "10.3.7.9", "latency": 54, "mac": "x"}]}},
                        {"test_type": "DNS", "test_status": "success", "test_detail": {"urls": [{"latency": 23}, {"latency": 41}, {"latency": 31}, {"latency": 13}]}},
                        {"test_type": "CURL", "test_status": "success", "test_detail": {"urls": [{"latency": 15, "response": "200"}, {"latency": 30, "response": "204"}, {"latency": 161, "response": "200"}, {"latency": 100, "response": "200"}]}},
                    ],
                },
                {"vlan": 20, "connectivity": [
                    {"test_type": "DHCP", "test_status": "failure", "test_detail": {"dhcpv4": {"state": "DHCP Unresponsive", "summary": "DHCP DISCOVER Sent. Retry count [4]"}}},
                    {"test_type": "ARP", "test_status": "not_validated", "test_detail": {}},
                    {"test_type": "DNS", "test_status": "not_validated", "test_detail": {}},
                    {"test_type": "CURL", "test_status": "not_validated", "test_detail": {}},
                ], "has_pcap": True, "pcap_url": "https://example/pcap"},
                {"vlan": 99, "connectivity": [], "has_pcap": False},
            ],
        },
        {"ap_mac": "x", "ap_name": "AP2", "end": None, "vlans": []},
    ]
}
ap_results = parse_test_details(raw)
print("APs parsed:", len(ap_results))
print("VLAN 7 status:", ap_results[0]["vlans"][0]["status"])
print("VLAN 7 DHCP summary:", ap_results[0]["vlans"][0]["tests"][0]["summary"])
print("VLAN 20 status:", ap_results[0]["vlans"][1]["status"])
print("VLAN 20 DHCP summary:", ap_results[0]["vlans"][1]["tests"][0]["summary"])
print("VLAN 99 status:", ap_results[0]["vlans"][2]["status"])
print("Summary counts:", compute_marvis_summary(ap_results))
print("AP counts:", derive_ap_counts(raw))
PY
```

Expected output:

```
APs parsed: 2
VLAN 7 status: pass
VLAN 7 DHCP summary: 10.3.7.18/24 · 1ms
VLAN 20 status: fail
VLAN 20 DHCP summary: DHCP Unresponsive · retry 4
VLAN 99 status: not_tested
Summary counts: {'pass': 4, 'fail': 1, 'warn': 0}
AP counts: (1, 2)
```

If anything mismatches, fix the parser and re-run before continuing.

- [ ] **Step 2.3: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add backend/app/services/marvis_service.py
git commit -m "feat(marvis): add parser, scorer, and rollup helpers"
```

---

## Task 3 — Backend: trigger + poll loop + WS broadcast in `marvis_service.py`

**Goal:** Add the I/O parts of the service — trigger the test, poll until done, broadcast `marvis_progress` after every poll, return the final result block.

**Files:**
- Modify: `backend/app/services/marvis_service.py` (append to the file from Task 2)

- [ ] **Step 3.1: Append constants and step-message formatter**

After the `derive_ap_counts` function, append:

```python
# ── Constants ─────────────────────────────────────────────────────────────

POLL_INTERVAL_FAST_S = 5
POLL_INTERVAL_SLOW_S = 15
SLOW_PHASE_DURATION_S = 120  # use 15s polls for first 120 s, then 5 s
HARD_TIMEOUT_S = 8 * 60      # abort and mark timeout if total run exceeds 8 min


# ── Live step message ─────────────────────────────────────────────────────

def format_step_message(parsed: list[dict], ap_done: int, ap_total: int) -> str:
    """Compute the running-screen message from a parsed snapshot.

    "Testing 1/2 APs · DNT-NTR-APB VLAN 8 — CURL" while in flight,
    "Finalizing results…" once every AP has an end timestamp.
    """
    if ap_total == 0:
        return "Waiting for first response…"
    if ap_done >= ap_total:
        return "Finalizing results…"

    # Find the first AP that's still in flight (no end timestamp).
    in_flight: dict | None = None
    for ap in parsed:
        if not ap.get("vlans"):
            continue  # no data yet at all
        # Has any vlan with non-empty tests? then this AP has started
        any_started = any(v.get("tests") for v in ap["vlans"])
        if any_started:
            # If every vlan rolled up to a non-pending state, the AP is done from
            # the parser's perspective — caller already picked it via end-timestamp,
            # but the parser doesn't see end. Just take the first AP whose vlans
            # are not all `not_tested`.
            in_flight = ap
            break

    if in_flight is None:
        return f"Testing {ap_done + 1}/{ap_total} APs · waiting on first response"

    # Find the latest test we've seen (last vlan with tests, last test in it)
    latest_vlan: dict | None = None
    latest_test: dict | None = None
    for v in in_flight["vlans"]:
        if v.get("tests"):
            latest_vlan = v
            latest_test = v["tests"][-1]
    if latest_vlan and latest_test:
        return (
            f"Testing {ap_done + 1}/{ap_total} APs · "
            f"{in_flight.get('ap_name', '?')} VLAN {latest_vlan.get('vlan')} — {latest_test.get('test_type')}"
        )
    return f"Testing {ap_done + 1}/{ap_total} APs · {in_flight.get('ap_name', '?')}"
```

- [ ] **Step 3.2: Append trigger and snapshot fetch**

Append:

```python
# ── HTTP wrappers ─────────────────────────────────────────────────────────

async def _trigger_test(session: Any, site_id: str) -> tuple[str | None, str | None]:
    """POST /api/v1/sites/{site_id}/synthetic_test → (test_id, error).

    `mist_post`/`mist_get` are synchronous in the mistapi SDK, so they're
    wrapped with asyncio.to_thread.
    """
    import asyncio
    uri = f"/api/v1/sites/{site_id}/synthetic_test"
    try:
        resp = await asyncio.to_thread(session.mist_post, uri, body={})
        if resp.status_code in (200, 201, 202):
            data = resp.data or {}
            test_id = data.get("id") if isinstance(data, dict) else None
            if test_id:
                return test_id, None
            return None, f"{resp.status_code} response without id"
        body = resp.data if isinstance(resp.data, str) else str(resp.data)
        return None, f"{resp.status_code}: {body[:200]}"
    except Exception as e:
        logger.warning("marvis_trigger_failed site_id=%s error=%s", site_id, e)
        return None, f"trigger exception: {e}"


async def _fetch_snapshot(session: Any, org_id: str, site_id: str, test_id: str) -> dict | None:
    """GET /api/v1/labs/orgs/{org_id}/synthetic_test → response.data, or None on error."""
    import asyncio
    uri = f"/api/v1/labs/orgs/{org_id}/synthetic_test"
    query = {
        "q": "test_details",
        "view": "table",
        "site_id": site_id,
        "test_id": test_id,
    }
    try:
        resp = await asyncio.to_thread(session.mist_get, uri, query=query)
        if resp.status_code == 200 and isinstance(resp.data, dict):
            return resp.data
        logger.debug("marvis_poll_status status=%s", resp.status_code)
        return None
    except Exception as e:
        logger.warning("marvis_poll_failed test_id=%s error=%s", test_id, e)
        return None


def _is_terminal(snapshot: dict) -> bool:
    """A snapshot is terminal when test_status == 'completed' OR (result in
    {success, failure} AND progress >= 100). See spec §4.3."""
    data = snapshot.get("data") or snapshot
    if data.get("test_status") == "completed":
        return True
    if data.get("result") in ("success", "failure"):
        try:
            return float(data.get("progress", 0)) >= 100
        except (TypeError, ValueError):
            return False
    return False
```

- [ ] **Step 3.3: Append the orchestrator**

Append:

```python
# ── Orchestrator ──────────────────────────────────────────────────────────

async def run_marvis_minis(
    session: Any,
    org_id: str,
    site_id: str,
    tracker: Any,
    progress_callback: Any,
    job_id: str,
) -> dict:
    """Trigger a Marvis Minis test, poll until complete, return the result block.

    Side effects:
      - Updates `tracker.steps['marvis_minis'].message` after every poll.
      - Broadcasts a `marvis_progress` WS event after every poll.

    Spec: docs/superpowers/specs/2026-05-01-marvis-minis-design.md §4.
    """
    import asyncio
    import time

    test_id, trigger_error = await _trigger_test(session, site_id)
    if not test_id:
        logger.warning("marvis_trigger_failed site_id=%s reason=%s", site_id, trigger_error)
        await tracker.update_step("marvis_minis", f"Trigger failed: {trigger_error}")
        return {
            "status": "trigger_failed",
            "trigger_error": trigger_error,
            "test_id": None,
            "started_at": int(time.time()),
            "duration_seconds": 0,
            "result": None,
            "summary": {"pass": 0, "fail": 0, "warn": 0},
            "ap_results": [],
        }

    started_at = int(time.time())
    start_monotonic = time.monotonic()
    last_snapshot: dict | None = None
    consecutive_errors = 0

    await tracker.update_step("marvis_minis", "Test triggered, waiting for first response…")

    while True:
        elapsed = time.monotonic() - start_monotonic
        if elapsed > HARD_TIMEOUT_S:
            logger.warning("marvis_timeout site_id=%s test_id=%s elapsed=%.0f", site_id, test_id, elapsed)
            parsed = parse_test_details((last_snapshot or {}).get("data") or {})
            return {
                "status": "timeout",
                "trigger_error": None,
                "test_id": test_id,
                "started_at": started_at,
                "duration_seconds": int(elapsed),
                "result": "timeout",
                "summary": compute_marvis_summary(parsed),
                "ap_results": parsed,
            }

        interval = POLL_INTERVAL_SLOW_S if elapsed < SLOW_PHASE_DURATION_S else POLL_INTERVAL_FAST_S
        await asyncio.sleep(interval)

        snapshot = await _fetch_snapshot(session, org_id, site_id, test_id)
        if snapshot is None:
            consecutive_errors += 1
            if consecutive_errors >= 3:
                logger.warning("marvis_poll_giving_up test_id=%s consecutive_errors=%d", test_id, consecutive_errors)
                parsed = parse_test_details((last_snapshot or {}).get("data") or {})
                return {
                    "status": "timeout",
                    "trigger_error": None,
                    "test_id": test_id,
                    "started_at": started_at,
                    "duration_seconds": int(time.monotonic() - start_monotonic),
                    "result": "poll_failed",
                    "summary": compute_marvis_summary(parsed),
                    "ap_results": parsed,
                }
            continue
        consecutive_errors = 0
        last_snapshot = snapshot
        data = snapshot.get("data") or snapshot

        parsed = parse_test_details(data)
        ap_done, ap_total = derive_ap_counts(snapshot)
        msg = format_step_message(parsed, ap_done, ap_total)
        await tracker.update_step("marvis_minis", msg)

        progress_value = data.get("progress", 0)
        try:
            progress_float = float(progress_value)
        except (TypeError, ValueError):
            progress_float = 0.0

        await progress_callback(job_id, {
            "type": "marvis_progress",
            "data": {
                "test_id": test_id,
                "progress": progress_float,
                "ap_count_done": ap_done,
                "ap_count_total": ap_total,
                "ap_results": parsed,
            },
        })

        if _is_terminal(snapshot):
            duration = int(time.monotonic() - start_monotonic)
            return {
                "status": "completed",
                "trigger_error": None,
                "test_id": test_id,
                "started_at": started_at,
                "duration_seconds": duration,
                "result": data.get("result"),
                "summary": compute_marvis_summary(parsed),
                "ap_results": parsed,
            }
```

- [ ] **Step 3.4: Smoke-test imports and types**

```bash
cd /Users/tmunzer/4_dev/mist_validation/backend
.venv/bin/python -c "from app.services.marvis_service import run_marvis_minis, parse_test_details, format_step_message, _is_terminal; print('ok')"
.venv/bin/python -c "from app.services.marvis_service import _is_terminal; print(_is_terminal({'data': {'test_status': 'completed'}}), _is_terminal({'data': {'result': 'in_progress', 'progress': 50}}), _is_terminal({'data': {'result': 'failure', 'progress': 100}}))"
```

Expected: `ok`, then `True False True`.

- [ ] **Step 3.5: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add backend/app/services/marvis_service.py
git commit -m "feat(marvis): add trigger, poll loop, and WS broadcast"
```

---

## Task 4 — Backend: integrate into `validation_service.py`

**Goal:** Wire `run_marvis_minis` into the orchestrator as the last step, give the tracker a Marvis ETA anchor, and fold the score into `_compute_summary`.

**Files:**
- Modify: `backend/app/services/validation_service.py:85-96` (`_STEPS`)
- Modify: `backend/app/services/validation_service.py:99-240` (`_ProgressTracker`)
- Modify: `backend/app/services/validation_service.py:243-563` (`run_post_deployment_validation`)
- Modify: `backend/app/services/validation_service.py:2346-2377` (`_compute_summary`)

- [ ] **Step 4.1: Add the new step**

In `_STEPS` (line 85-96), change:

```python
_STEPS = [
    ("site_info", "Site Configuration"),
    ...
    ("config_errors", "Config Command Errors"),
]
```

to:

```python
_STEPS = [
    ("site_info", "Site Configuration"),
    ("templates", "Templates & WLANs"),
    ("variables", "Template Variables"),
    ("config_events", "Configuration Events"),
    ("device_events", "Device Events"),
    ("aps", "Access Points"),
    ("switches", "Switches"),
    ("gateways", "Gateways"),
    ("cable_tests", "Cable Tests"),
    ("config_errors", "Config Command Errors"),
    ("marvis_minis", "Marvis Minis"),
]
```

- [ ] **Step 4.2: Add Marvis ETA anchor to `_ProgressTracker`**

In `_ProgressTracker.__init__` (line 122-135), append two attributes after `self._cable_tests_done = 0`:

```python
        self._marvis_finish_at: float | None = None  # monotonic seconds; set at marvis step start
        self.MARVIS_TOTAL_SECONDS = 240  # ~4 min budget
```

After `start_cable_test_phase()` (around line 183-191), add the new method:

```python
    def start_marvis_phase(self) -> None:
        """Anchor the marvis-minis finish time at the moment the step actually starts."""
        self._marvis_finish_at = time.monotonic() + self.MARVIS_TOTAL_SECONDS
```

In `_eta_seconds()` (line 193-215), change the ETA formula. Replace:

```python
    def _eta_seconds(self) -> int | None:
        # Without registered costs the formula has nothing to anchor on — return None so
        # the UI hides the ETA instead of pinning at "0s".
        if not self._step_api_cost and self._cable_test_max_ports == 0:
            return None
        api_remaining = sum(
            self._step_api_cost.get(sid, 0)
            for sid, step in self.steps.items()
            if step["status"] != "completed"
        )
        # Cable contribution: estimate while the step is pending, anchored countdown
        # while running. The else-fallback covers the brief running-without-anchor window
        # between `start_step("cable_tests")` (which broadcasts) and `start_cable_test_phase()`
        # (which sets the anchor without broadcasting) — without it the ETA dips to 0
        # at the start_step broadcast, then jumps back up on the next port-complete tick.
        cable_remaining = 0
        cable_status = self.steps.get("cable_tests", {}).get("status")
        if cable_status in ("pending", "running"):
            if self._cable_test_finish_at is not None:
                cable_remaining = max(0, int(self._cable_test_finish_at - time.monotonic()))
            else:
                cable_remaining = self._cable_test_max_ports * self.CABLE_SECONDS_PER_PORT
        return api_remaining * self.API_SECONDS_PER_CALL + cable_remaining
```

with the same logic plus a marvis term:

```python
    def _eta_seconds(self) -> int | None:
        # Without registered costs and no wall-clock anchors, the formula has nothing
        # to work from — return None so the UI hides the ETA instead of pinning at 0s.
        marvis_step_status = self.steps.get("marvis_minis", {}).get("status")
        marvis_active = marvis_step_status in ("pending", "running") and (
            self._marvis_finish_at is not None
            or self._step_api_cost.get("marvis_minis", 0) > 0
        )
        if (
            not self._step_api_cost
            and self._cable_test_max_ports == 0
            and not marvis_active
        ):
            return None
        api_remaining = sum(
            self._step_api_cost.get(sid, 0)
            for sid, step in self.steps.items()
            if step["status"] != "completed"
        )
        cable_remaining = 0
        cable_status = self.steps.get("cable_tests", {}).get("status")
        if cable_status in ("pending", "running"):
            if self._cable_test_finish_at is not None:
                cable_remaining = max(0, int(self._cable_test_finish_at - time.monotonic()))
            else:
                cable_remaining = self._cable_test_max_ports * self.CABLE_SECONDS_PER_PORT

        marvis_remaining = 0
        marvis_status = self.steps.get("marvis_minis", {}).get("status")
        if marvis_status in ("pending", "running"):
            if self._marvis_finish_at is not None:
                marvis_remaining = max(0, int(self._marvis_finish_at - time.monotonic()))
            else:
                # Pre-anchor: only count if the user actually opted in. We can't
                # distinguish here, so we err on showing the budget — caller must
                # not register the marvis step at all if it's skipped (Task 4.3
                # uses start_step + complete_step with "Skipped" only when
                # include_marvis_minis is False, and never calls start_marvis_phase).
                marvis_remaining = self.MARVIS_TOTAL_SECONDS

        return api_remaining * self.API_SECONDS_PER_CALL + cable_remaining + marvis_remaining
```

- [ ] **Step 4.3: Add `include_marvis_minis` to the orchestrator and run the step**

In `run_post_deployment_validation` signature (line 243-254), add:

```python
async def run_post_deployment_validation(
    job_id: str,
    site_id: str,
    cloud_region: str,
    org_id: str,
    include_cable_tests: bool = False,
    include_config_errors: bool = False,
    include_marvis_minis: bool = False,
    progress_callback=None,
    token: str | None = None,
    cookies: dict | None = None,
    csrftoken: str | None = None,
) -> None:
```

In the per-step API cost block (around line 311-322), only register the marvis cost when the user opted in. Change:

```python
            tracker.set_step_api_costs({
                "site_info": 3,
                "templates": 5,
                "variables": 0,
                "config_events": 1,
                "device_events": 0,
                "aps": 4 + n_ap,
                "switches": n_sw * 2,
                "gateways": n_gw * 3,
                "cable_tests": 0,
                "config_errors": (n_sw + n_gw) if include_config_errors else 0,
            })
```

to:

```python
            tracker.set_step_api_costs({
                "site_info": 3,
                "templates": 5,
                "variables": 0,
                "config_events": 1,
                "device_events": 0,
                "aps": 4 + n_ap,
                "switches": n_sw * 2,
                "gateways": n_gw * 3,
                "cable_tests": 0,
                "config_errors": (n_sw + n_gw) if include_config_errors else 0,
                "marvis_minis": 1 if include_marvis_minis else 0,
            })
```

After the `config_errors` step block (around line 519-529), add the Marvis block:

```python
        # Step 10: Marvis Minis (opt-in)
        from app.services import marvis_service
        if include_marvis_minis:
            await tracker.start_step("marvis_minis", "Triggering Marvis Minis…")
            tracker.start_marvis_phase()
            result["marvis_minis"] = await marvis_service.run_marvis_minis(
                session=session,
                org_id=mist.org_id,
                site_id=site_id,
                tracker=tracker,
                progress_callback=progress_callback,
                job_id=job_id,
            )
            mm_status = result["marvis_minis"].get("status")
            if mm_status == "completed":
                await tracker.complete_step("marvis_minis", "Tests complete")
            elif mm_status == "trigger_failed":
                # Mark step failed but report still completes
                tracker.steps["marvis_minis"]["status"] = "failed"
                tracker.steps["marvis_minis"]["message"] = (
                    f"Trigger failed: {result['marvis_minis'].get('trigger_error', 'unknown')}"
                )
                await tracker._broadcast()
            else:  # timeout / other
                tracker.steps["marvis_minis"]["status"] = "failed"
                tracker.steps["marvis_minis"]["message"] = f"Marvis Minis {mm_status}"
                await tracker._broadcast()
        else:
            await tracker.start_step("marvis_minis", "Skipped (opt-in)")
            await tracker.complete_step("marvis_minis", "Skipped (opt-in)")
```

(`session` is already in scope from earlier in the function; double-check the variable name. `mist.org_id` is also in scope.)

- [ ] **Step 4.4: Update `_compute_summary` to count Marvis tests**

In `_compute_summary` (line 2346-2377), append before the `return counts`:

```python
    # Marvis Minis tests (each AP × VLAN × test counts toward score per spec §5.4)
    marvis = result.get("marvis_minis")
    if isinstance(marvis, dict):
        for ap in marvis.get("ap_results", []) or []:
            for vlan in ap.get("vlans", []) or []:
                for test in vlan.get("tests", []) or []:
                    s = test.get("status", "info")
                    if s in counts:
                        counts[s] += 1

    return counts
```

(Replace the existing `return counts` line with the block above followed by the return.)

- [ ] **Step 4.5: Manual verification — orchestrator imports and signature**

```bash
cd /Users/tmunzer/4_dev/mist_validation/backend
.venv/bin/python -c "from app.services.validation_service import run_post_deployment_validation, _ProgressTracker; import inspect; sig = inspect.signature(run_post_deployment_validation); print('include_marvis_minis' in sig.parameters)"
.venv/bin/python -c "from app.services.validation_service import _ProgressTracker; t = _ProgressTracker('x', None); print(hasattr(t, 'start_marvis_phase'), hasattr(t, '_marvis_finish_at'))"
```

Expected: `True` then `True True`.

Run a quick `_compute_summary` check using the parser output from Task 2:

```bash
cd /Users/tmunzer/4_dev/mist_validation/backend
.venv/bin/python <<'PY'
from app.services.validation_service import _compute_summary
from app.services.marvis_service import parse_test_details

raw = {"test_details": [{"ap_mac": "x", "ap_name": "A", "end": 1, "vlans": [
    {"vlan": 7, "connectivity": [
        {"test_type": "DHCP", "test_status": "success", "test_detail": {}},
        {"test_type": "ARP", "test_status": "failure", "test_detail": {}},
    ]}
]}]}
result = {
    "template_variables": [],
    "aps": [], "switches": [], "gateways": [],
    "marvis_minis": {"ap_results": parse_test_details(raw)},
}
print(_compute_summary(result))
PY
```

Expected: `{'pass': 1, 'fail': 1, 'warn': 0, 'info': 0}`.

- [ ] **Step 4.6: End-to-end smoke test (no Marvis trigger; flag off)**

Start the backend and frontend. With `include_marvis_minis=False` (the default), kick off a normal report against any site you have access to:

```bash
# Terminal A
cd /Users/tmunzer/4_dev/mist_validation/backend
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
# Terminal B
cd /Users/tmunzer/4_dev/mist_validation/frontend
npm start
```

In the browser, run a normal site report (do NOT enable any Marvis toggle yet — none exists in the UI; this is purely the backend regression check). Verify the report completes with the new "Marvis Minis" step showing "Skipped (opt-in)".

- [ ] **Step 4.7: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add backend/app/services/validation_service.py
git commit -m "feat(marvis): integrate marvis_minis as the last validation step"
```

---

## Task 5 — Backend: PDF export section

**Goal:** Render a "Marvis Minis Synthetic Tests" table in the PDF report, one row per AP × VLAN with worst-status colored cells.

**Files:**
- Modify: `backend/app/services/export_service.py`

- [ ] **Step 5.1: Locate the PDF generator**

```bash
grep -n "def generate_pdf\|def _add_section\|story.append" /Users/tmunzer/4_dev/mist_validation/backend/app/services/export_service.py | head -30
```

Note the function `generate_pdf(job: dict) -> bytes` and the section-rendering pattern used for existing AP/Switch/Gateway tables.

- [ ] **Step 5.2: Add a Marvis section**

Inside `generate_pdf`, after the existing device sections and before the document is built, add:

```python
    marvis = (job.get("result") or {}).get("marvis_minis")
    if isinstance(marvis, dict) and marvis.get("status") == "completed":
        story.append(PageBreak())
        story.append(Paragraph("Marvis Minis Synthetic Tests", styles["Heading1"]))
        story.append(Spacer(1, 8))

        # Summary line
        s = marvis.get("summary", {})
        summary_line = (
            f"{s.get('pass', 0)} pass · {s.get('fail', 0)} fail · "
            f"{s.get('warn', 0)} warn · duration {marvis.get('duration_seconds', 0)}s"
        )
        story.append(Paragraph(summary_line, styles["Normal"]))
        story.append(Spacer(1, 12))

        for ap in marvis.get("ap_results", []):
            ap_title = f"{ap.get('ap_name') or '(unnamed)'} — {ap.get('switch_name', '?')}/{ap.get('switch_port', '?')}"
            story.append(Paragraph(ap_title, styles["Heading3"]))

            header = ["VLAN", "DHCP", "ARP", "DNS", "CURL"]
            rows: list[list[str]] = [header]
            row_colors: list[list[colors.Color | None]] = [[None] * 5]
            for vlan in ap.get("vlans", []):
                row = [str(vlan.get("vlan", "?"))]
                color_row: list[colors.Color | None] = [None]
                tests_by_type = {t.get("test_type"): t for t in vlan.get("tests", [])}
                for tt in ("DHCP", "ARP", "DNS", "CURL"):
                    test = tests_by_type.get(tt)
                    if not test:
                        row.append("—")
                        color_row.append(None)
                        continue
                    status = test.get("status", "info")
                    row.append(test.get("summary", status))
                    color_row.append({
                        "pass": colors.HexColor("#dff5e2"),
                        "fail": colors.HexColor("#fde2e1"),
                        "warn": colors.HexColor("#fff4d6"),
                    }.get(status))
                rows.append(row)
                row_colors.append(color_row)

            tbl = Table(rows, colWidths=[40, 110, 110, 110, 110])
            style_cmds = [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f3f5")),
                ("BOX", (0, 0), (-1, -1), 0.25, colors.HexColor("#dee2e6")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dee2e6")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
            for r_idx, color_row in enumerate(row_colors):
                for c_idx, color in enumerate(color_row):
                    if color is not None:
                        style_cmds.append(("BACKGROUND", (c_idx, r_idx), (c_idx, r_idx), color))
            tbl.setStyle(TableStyle(style_cmds))
            story.append(tbl)
            story.append(Spacer(1, 12))

    # Trigger failure / timeout — render a single warning paragraph
    elif isinstance(marvis, dict) and marvis.get("status") in ("trigger_failed", "timeout"):
        story.append(PageBreak())
        story.append(Paragraph("Marvis Minis Synthetic Tests", styles["Heading1"]))
        msg = marvis.get("trigger_error") or f"Test {marvis.get('status')} after {marvis.get('duration_seconds', 0)}s"
        story.append(Paragraph(f"<b>Status:</b> {marvis.get('status')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Reason:</b> {msg}", styles["Normal"]))
```

(Make sure `PageBreak`, `Spacer`, `Table`, `TableStyle`, `Paragraph`, `colors` are already imported at the top of `export_service.py` — they will be, since the existing PDF code uses them. If not, add to the existing ReportLab imports.)

- [ ] **Step 5.3: Smoke-test PDF generation with a synthetic Marvis result**

```bash
cd /Users/tmunzer/4_dev/mist_validation/backend
.venv/bin/python <<'PY'
from app.services.export_service import generate_pdf
job = {
    "id": "x", "org_name": "demo", "site_name": "demo-site", "status": "completed",
    "include_cable_tests": 0, "include_config_errors": 0,
    "result": {
        "site_info": {"site_name": "demo-site", "site_address": "", "site_groups": [], "templates": [], "org_wlans": [], "site_wlans": [], "device_summary": {}},
        "template_variables": [],
        "aps": [], "switches": [], "gateways": [],
        "summary": {"pass": 1, "fail": 1, "warn": 0, "info": 0},
        "marvis_minis": {
            "status": "completed",
            "test_id": "x", "started_at": 0, "duration_seconds": 200,
            "result": "failure", "trigger_error": None,
            "summary": {"pass": 1, "fail": 1, "warn": 0},
            "ap_results": [
                {"ap_mac": "x", "ap_name": "AP1", "switch_name": "SW1", "switch_port": "ge-0/0/0", "vlans": [
                    {"vlan": 7, "status": "pass", "has_pcap": False, "pcap_url": None, "tests": [
                        {"test_type": "DHCP", "status": "pass", "summary": "10.3.7.18/24 · 1ms", "detail": {}},
                        {"test_type": "ARP", "status": "fail", "summary": "ARP failed", "detail": {}},
                        {"test_type": "DNS", "status": "info", "summary": "Not validated", "detail": {}},
                        {"test_type": "CURL", "status": "info", "summary": "Not validated", "detail": {}},
                    ]},
                ]},
            ],
        },
    },
}
b = generate_pdf(job)
print("PDF bytes:", len(b))
open("/tmp/marvis-test.pdf", "wb").write(b)
print("Wrote /tmp/marvis-test.pdf")
PY
```

Expected: a non-zero byte count. Open `/tmp/marvis-test.pdf` and visually verify the new section is present, with the AP header, VLAN row, and tinted cells.

- [ ] **Step 5.4: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add backend/app/services/export_service.py
git commit -m "feat(marvis): add Marvis Minis section to PDF export"
```

---

## Task 6 — Backend: CSV export file

**Goal:** Add a `marvis_minis.csv` file to the export zip, one row per `{ap, vlan, test_type}`.

**Files:**
- Modify: `backend/app/services/export_service.py`

- [ ] **Step 6.1: Locate the CSV zip generator**

```bash
grep -n "def generate_csv_zip\|zip.writestr\|csv.writer\|StringIO" /Users/tmunzer/4_dev/mist_validation/backend/app/services/export_service.py | head -20
```

Note the pattern: typically `BytesIO + ZipFile + StringIO + csv.writer`, then `zip.writestr(filename, csv_str)` for each section.

- [ ] **Step 6.2: Add the CSV section**

Inside `generate_csv_zip` (or its helper), after the existing CSV files are written to the zip, add:

```python
    marvis = (job.get("result") or {}).get("marvis_minis")
    marvis_buf = io.StringIO()
    marvis_writer = csv.writer(marvis_buf)
    marvis_writer.writerow([
        "ap_name", "ap_mac", "switch_name", "switch_port",
        "vlan", "test_type", "status", "summary", "has_pcap"
    ])
    if isinstance(marvis, dict) and marvis.get("status") == "completed":
        for ap in marvis.get("ap_results", []):
            for vlan in ap.get("vlans", []):
                if not vlan.get("tests"):
                    marvis_writer.writerow([
                        ap.get("ap_name", ""), ap.get("ap_mac", ""),
                        ap.get("switch_name", ""), ap.get("switch_port", ""),
                        vlan.get("vlan", ""), "", "not_tested", "",
                        "true" if vlan.get("has_pcap") else "false",
                    ])
                    continue
                for test in vlan.get("tests", []):
                    marvis_writer.writerow([
                        ap.get("ap_name", ""), ap.get("ap_mac", ""),
                        ap.get("switch_name", ""), ap.get("switch_port", ""),
                        vlan.get("vlan", ""),
                        test.get("test_type", ""),
                        test.get("status", ""),
                        test.get("summary", ""),
                        "true" if vlan.get("has_pcap") else "false",
                    ])
    zip_file.writestr("marvis_minis.csv", marvis_buf.getvalue())
```

(Names `io`, `csv`, `zip_file` — match whatever the existing function uses. Check the surrounding code; the pattern is consistent.)

- [ ] **Step 6.3: Smoke-test**

```bash
cd /Users/tmunzer/4_dev/mist_validation/backend
.venv/bin/python <<'PY'
import zipfile, io
from app.services.export_service import generate_csv_zip
job = {
    "id": "x", "org_name": "demo", "site_name": "demo-site", "status": "completed",
    "include_cable_tests": 0, "include_config_errors": 0,
    "result": {
        "site_info": {"site_name": "demo-site", "site_address": "", "site_groups": [], "templates": [], "org_wlans": [], "site_wlans": [], "device_summary": {}},
        "template_variables": [],
        "aps": [], "switches": [], "gateways": [],
        "summary": {"pass": 0, "fail": 0, "warn": 0, "info": 0},
        "marvis_minis": {
            "status": "completed", "test_id": "x", "started_at": 0, "duration_seconds": 200,
            "result": "success", "trigger_error": None,
            "summary": {"pass": 4, "fail": 0, "warn": 0},
            "ap_results": [
                {"ap_mac": "x", "ap_name": "AP1", "switch_name": "SW1", "switch_port": "ge-0/0/0", "vlans": [
                    {"vlan": 7, "status": "pass", "has_pcap": False, "pcap_url": None, "tests": [
                        {"test_type": "DHCP", "status": "pass", "summary": "ok", "detail": {}},
                        {"test_type": "ARP", "status": "pass", "summary": "ok", "detail": {}},
                    ]},
                    {"vlan": 99, "status": "not_tested", "has_pcap": False, "pcap_url": None, "tests": []},
                ]},
            ],
        },
    },
}
b = generate_csv_zip(job)
zf = zipfile.ZipFile(io.BytesIO(b))
print("Files:", zf.namelist())
print(zf.read("marvis_minis.csv").decode())
PY
```

Expected: `marvis_minis.csv` is in the file list, and its contents include 1 header row + 2 test rows + 1 not_tested row.

- [ ] **Step 6.4: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add backend/app/services/export_service.py
git commit -m "feat(marvis): add marvis_minis.csv to export zip"
```

---

## Task 7 — Frontend: opt-in toggle on site selector

**Goal:** Add a "Marvis Minis" opt-in card next to "Cable Diagnostics" with the same gating pattern (write-access required), and submit the new flag in the create-report body.

**Files:**
- Modify: `frontend/src/app/features/site-selector/site-selector.component.ts`
- Modify: `frontend/src/app/features/site-selector/site-selector.component.html`

- [ ] **Step 7.1: Add the form control and write-access logic**

In `site-selector.component.ts`, find the existing form controls (around line 103-104):

```typescript
  cableTestsCtrl = this.fb.control(false);
  configErrorsCtrl = this.fb.control(false);
```

Append:

```typescript
  marvisMinisCtrl = this.fb.control(false);
```

Find the `cableTestsAllowed` / `cableTestsDisabledReason` computed signals (search for `cableTestsAllowed` in the file). Add an analogous pair right after:

```typescript
  marvisMinisAllowed = computed(() => {
    return this.scope() === 'site' && this.canWrite();
  });
  marvisMinisDisabledReason = computed(() => {
    if (this.scope() !== 'site') return '';
    if (!this.canWrite()) return 'Marvis Minis requires write access to the organization.';
    return '';
  });
```

(`canWrite()` is the same signal already used by cable tests. If it doesn't exist yet, find how `cableTestsAllowed` accesses write status and reuse the same accessor.)

In the scope-change handler (search for `cableTestsCtrl.disable` and `cableTestsCtrl.enable` — around line 233-236 and 314-318), add the matching enable/disable for `marvisMinisCtrl`:

```typescript
        // After existing cableTestsCtrl handling:
        if (!this.marvisMinisAllowed()) {
          this.marvisMinisCtrl.setValue(false, { emitEvent: false });
          this.marvisMinisCtrl.disable({ emitEvent: false });
        } else {
          this.marvisMinisCtrl.enable({ emitEvent: false });
        }
```

Locate the `body[...] = this.cableTestsCtrl.value` line (around line 385). Add:

```typescript
        body['include_cable_tests'] = this.cableTestsCtrl.value;
        body['include_marvis_minis'] = this.marvisMinisCtrl.value;
```

Locate any `valueChanges.subscribe` for `cableTestsCtrl` (line 243). If a budget refetch is triggered by it, replicate the same pattern for `marvisMinisCtrl` — Marvis doesn't impact the API budget, so you can skip the refetch:

```typescript
    this.marvisMinisCtrl.valueChanges.subscribe(() => {
      // No budget impact; only the run cost (wall-clock) matters.
    });
```

(Or omit this subscription entirely if there's no side effect needed.)

- [ ] **Step 7.2: Add the opt-in card to the HTML**

In `site-selector.component.html`, find the existing "Cable diagnostics (TDR)" card block (line 273-315). After it (still inside `.scope-body`), add:

```html
        <!-- Optional: Marvis Minis -->
        <div
          class="check-group highlight"
          [class.unavailable]="scope() === 'org'"
        >
          <div class="cg-head">
            <div class="cg-text">
              <div class="cg-title">
                Marvis Minis
                <span class="cg-badge heavy">HEAVY</span>
                <span class="cg-badge">~4 min</span>
              </div>
              <div class="cg-hint">
                Synthetic DHCP / ARP / DNS / CURL tests across active VLANs · per-site only
              </div>
            </div>
            @if (scope() === 'org') {
              <span class="cg-na">NOT IN ORG-WIDE</span>
            }
          </div>
          @if (scope() === 'site') {
            <div class="cg-row">
              <mat-checkbox
                [formControl]="marvisMinisCtrl"
                [matTooltip]="marvisMinisDisabledReason()"
              >
                <span class="opt-label">Run Marvis Minis synthetic tests</span>
                <span class="opt-hint"
                  >Requires write access · Marvis subscription</span
                >
              </mat-checkbox>
              @if (selectedSite() && marvisMinisDisabledReason()) {
                <div class="info-block">
                  <mat-icon>info</mat-icon>
                  <span>{{ marvisMinisDisabledReason() }}</span>
                </div>
              } @else if (selectedSite() && marvisMinisCtrl.value) {
                <div class="warn-block">
                  <mat-icon>warning</mat-icon>
                  <span>Test runs ~4 minutes after all other checks complete.</span>
                </div>
              }
            </div>
          }
        </div>
```

- [ ] **Step 7.3: Type-check the frontend**

```bash
cd /Users/tmunzer/4_dev/mist_validation/frontend
npm run build
```

Expected: a clean build (no Angular template errors). If errors mention `marvisMinisAllowed` or `canWrite`, locate the existing cable-tests version and copy its accessor pattern exactly.

- [ ] **Step 7.4: Manual verification**

Start the dev servers (`npm start` + `uvicorn`). In the site selector with a write-enabled session and a site selected, the new card should be visible, the checkbox toggleable, and the "~4 min" badge present. Toggle it on and click "Run validation". The backend should accept the request and start a job (verify in the browser's Network tab that the POST body includes `include_marvis_minis: true`).

The job will run through; the new step "Marvis Minis" will trigger and call the API. **Don't worry if the test fails** — frontend rendering of results is added in Tasks 9-11.

- [ ] **Step 7.5: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add frontend/src/app/features/site-selector/site-selector.component.ts frontend/src/app/features/site-selector/site-selector.component.html
git commit -m "feat(marvis): add Marvis Minis opt-in toggle on site selector"
```

---

## Task 8 — Frontend: running-screen phase mapping

**Goal:** Group the new `marvis_minis` step under "Diagnostics" in the running screen.

**Files:**
- Modify: `frontend/src/app/features/running-screen/running-screen.component.ts:40-61`

- [ ] **Step 8.1: Add the phase entry**

In `PHASE_BY_STEP` (line 40-61), add the new entry next to `cable_tests` and `config_errors`:

```typescript
  cable_tests: 'Diagnostics',
  config_errors: 'Diagnostics',
  marvis_minis: 'Diagnostics',
```

- [ ] **Step 8.2: Verify build**

```bash
cd /Users/tmunzer/4_dev/mist_validation/frontend
npm run build
```

Expected: clean build.

- [ ] **Step 8.3: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add frontend/src/app/features/running-screen/running-screen.component.ts
git commit -m "feat(marvis): group marvis_minis step under Diagnostics phase"
```

---

## Task 9 — Frontend: `marvis-matrix` component (display only)

**Goal:** Build the AP × VLAN grid component that the report view will mount. Pure display: takes a data input, emits a cell-click event.

**Files:**
- Create: `frontend/src/app/features/report-view/marvis-matrix.component.ts`
- Create: `frontend/src/app/features/report-view/marvis-matrix.component.html`
- Create: `frontend/src/app/features/report-view/marvis-matrix.component.scss`

- [ ] **Step 9.1: Define the shared types and component class**

Create `marvis-matrix.component.ts`:

```typescript
import { Component, EventEmitter, Output, computed, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';

export type MarvisCellStatus = 'pass' | 'warn' | 'fail' | 'info' | 'not_tested' | 'pending';

export interface MarvisTest {
  test_type: 'DHCP' | 'ARP' | 'DNS' | 'CURL' | string;
  status: 'pass' | 'fail' | 'warn' | 'info';
  summary: string;
  detail: Record<string, unknown>;
}

export interface MarvisVlan {
  vlan: number | string;
  status: 'pass' | 'warn' | 'fail' | 'info' | 'not_tested';
  has_pcap: boolean;
  pcap_url: string | null;
  tests: MarvisTest[];
}

export interface MarvisAp {
  ap_mac: string;
  ap_name: string;
  switch_name: string;
  switch_port: string;
  vlans: MarvisVlan[];
}

export interface MarvisResult {
  status: 'completed' | 'trigger_failed' | 'timeout';
  test_id: string | null;
  duration_seconds: number;
  started_at: number;
  result: string | null;
  summary: { pass: number; fail: number; warn: number };
  trigger_error: string | null;
  ap_results: MarvisAp[];
}

export interface MarvisLiveSnapshot {
  test_id: string;
  progress: number;
  ap_count_done: number;
  ap_count_total: number;
  ap_results: MarvisAp[];
}

export interface MarvisCellClick {
  ap: MarvisAp;
  vlan: MarvisVlan;
}

@Component({
  selector: 'app-marvis-matrix',
  standalone: true,
  imports: [MatIconModule, MatProgressSpinnerModule, MatTooltipModule],
  templateUrl: './marvis-matrix.component.html',
  styleUrl: './marvis-matrix.component.scss',
})
export class MarvisMatrixComponent {
  data = input.required<MarvisResult | MarvisLiveSnapshot | null>();
  // True when this is a partial in-progress snapshot (from WS), false for a final result.
  live = input<boolean>(false);

  @Output() cellClick = new EventEmitter<MarvisCellClick>();

  apResults = computed<MarvisAp[]>(() => this.data()?.ap_results ?? []);

  vlanIds = computed<(number | string)[]>(() => {
    const ids = new Set<number | string>();
    for (const ap of this.apResults()) {
      for (const v of ap.vlans ?? []) {
        if (v.vlan !== null && v.vlan !== undefined) ids.add(v.vlan);
      }
    }
    return Array.from(ids).sort((a, b) => Number(a) - Number(b));
  });

  vlanByAp(ap: MarvisAp, vlanId: number | string): MarvisVlan | null {
    return ap.vlans.find((v) => v.vlan === vlanId) ?? null;
  }

  // Cell display status:
  //  - vlan exists with non-empty tests → vlan.status (pass/warn/fail/info/not_tested)
  //  - vlan exists with empty tests in live mode → 'pending'
  //  - vlan missing entirely from this AP → 'pending' (live) or 'not_tested' (final)
  cellStatus(ap: MarvisAp, vlanId: number | string): MarvisCellStatus {
    const vlan = this.vlanByAp(ap, vlanId);
    if (!vlan) return this.live() ? 'pending' : 'not_tested';
    if (vlan.status === 'not_tested' && this.live() && vlan.tests.length === 0) {
      return 'pending';
    }
    return vlan.status;
  }

  cellTooltip(ap: MarvisAp, vlanId: number | string): string {
    const vlan = this.vlanByAp(ap, vlanId);
    if (!vlan) return `VLAN ${vlanId} · waiting…`;
    if (!vlan.tests.length) return `VLAN ${vlanId} · not tested`;
    const failures = vlan.tests.filter((t) => t.status === 'fail').map((t) => t.test_type);
    if (failures.length) return `VLAN ${vlanId} · failed: ${failures.join(', ')}`;
    return `VLAN ${vlanId} · all tests passed`;
  }

  onCellClick(ap: MarvisAp, vlanId: number | string): void {
    const vlan = this.vlanByAp(ap, vlanId);
    if (!vlan || !vlan.tests.length) return;  // inert for not_tested / missing
    this.cellClick.emit({ ap, vlan });
  }

  iconFor(status: MarvisCellStatus): string {
    return {
      pass: 'check_circle',
      fail: 'cancel',
      warn: 'warning',
      info: 'remove',
      not_tested: 'remove',
      pending: 'hourglass_empty',
    }[status];
  }
}
```

- [ ] **Step 9.2: Write the template**

Create `marvis-matrix.component.html`:

```html
<div class="marvis-matrix">
  @if (apResults().length === 0) {
    <div class="marvis-empty">
      @if (live()) {
        <mat-spinner diameter="20"></mat-spinner>
        <span>Waiting for first AP response…</span>
      } @else {
        <span>No tested APs in this run.</span>
      }
    </div>
  } @else {
    <table class="marvis-grid">
      <thead>
        <tr>
          <th class="ap-cell">Access Point</th>
          @for (vid of vlanIds(); track vid) {
            <th class="vlan-cell">VLAN {{ vid }}</th>
          }
        </tr>
      </thead>
      <tbody>
        @for (ap of apResults(); track ap.ap_mac) {
          <tr>
            <td class="ap-cell">
              <div class="ap-name">{{ ap.ap_name || '(unnamed)' }}</div>
              <div class="ap-meta">{{ ap.switch_name }} · {{ ap.switch_port }}</div>
            </td>
            @for (vid of vlanIds(); track vid) {
              <td
                class="vlan-cell status-{{ cellStatus(ap, vid) }}"
                [class.clickable]="cellStatus(ap, vid) !== 'not_tested' && cellStatus(ap, vid) !== 'pending' && cellStatus(ap, vid) !== 'info'"
                [matTooltip]="cellTooltip(ap, vid)"
                (click)="onCellClick(ap, vid)"
              >
                @if (cellStatus(ap, vid) === 'pending') {
                  <mat-spinner diameter="16"></mat-spinner>
                } @else {
                  <mat-icon>{{ iconFor(cellStatus(ap, vid)) }}</mat-icon>
                }
                @if (vlanByAp(ap, vid)?.has_pcap) {
                  <span class="pcap-chip" matTooltip="PCAP available">PCAP</span>
                }
              </td>
            }
          </tr>
        }
      </tbody>
    </table>
  }
</div>
```

- [ ] **Step 9.3: Write the styles**

Create `marvis-matrix.component.scss`:

```scss
.marvis-matrix {
  width: 100%;
  overflow-x: auto;
}

.marvis-empty {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 24px;
  color: var(--mv-text-muted, #6c757d);
  font-size: 14px;
}

.marvis-grid {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
}

.marvis-grid th,
.marvis-grid td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--mv-border, #e9ecef);
  vertical-align: middle;
  text-align: center;
}

.marvis-grid th {
  background: var(--mv-surface-2, #f8f9fa);
  font-weight: 600;
  color: var(--mv-text, #212529);
  white-space: nowrap;
}

.marvis-grid th.ap-cell,
.marvis-grid td.ap-cell {
  text-align: left;
  position: sticky;
  left: 0;
  background: var(--mv-surface, #fff);
  z-index: 1;
  min-width: 220px;
}

.marvis-grid th.ap-cell {
  background: var(--mv-surface-2, #f8f9fa);
}

.ap-name {
  font-weight: 600;
  color: var(--mv-text, #212529);
}

.ap-meta {
  font-size: 12px;
  color: var(--mv-text-muted, #6c757d);
}

td.vlan-cell {
  position: relative;
  cursor: default;

  mat-icon {
    font-size: 22px;
    height: 22px;
    width: 22px;
    line-height: 22px;
    vertical-align: middle;
  }

  &.clickable {
    cursor: pointer;

    &:hover {
      background: var(--mv-surface-hover, #f1f3f5);
    }
  }

  &.status-pass mat-icon { color: #2f9e44; }
  &.status-fail mat-icon { color: #e03131; }
  &.status-warn mat-icon { color: #f59f00; }
  &.status-info mat-icon,
  &.status-not_tested mat-icon { color: #adb5bd; }
}

.pcap-chip {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.4px;
  background: #fff3bf;
  color: #5c3d00;
  vertical-align: middle;
}
```

- [ ] **Step 9.4: Verify build**

```bash
cd /Users/tmunzer/4_dev/mist_validation/frontend
npm run build
```

Expected: clean build. The component isn't yet mounted — that comes in Task 11.

- [ ] **Step 9.5: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add frontend/src/app/features/report-view/marvis-matrix.component.ts frontend/src/app/features/report-view/marvis-matrix.component.html frontend/src/app/features/report-view/marvis-matrix.component.scss
git commit -m "feat(marvis): add AP×VLAN matrix component"
```

---

## Task 10 — Frontend: `marvis-detail-dialog` component

**Goal:** Build the side-drawer dialog that opens when a matrix cell is clicked, showing the 4 sub-tests and the PCAP link.

**Files:**
- Create: `frontend/src/app/features/report-view/marvis-detail-dialog.component.ts`
- Create: `frontend/src/app/features/report-view/marvis-detail-dialog.component.html`
- Create: `frontend/src/app/features/report-view/marvis-detail-dialog.component.scss`

- [ ] **Step 10.1: Component class**

Create `marvis-detail-dialog.component.ts`:

```typescript
import { Component, Inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatExpansionModule } from '@angular/material/expansion';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';
import { MarvisAp, MarvisVlan, MarvisTest } from './marvis-matrix.component';

export interface MarvisDetailData {
  ap: MarvisAp;
  vlan: MarvisVlan;
}

@Component({
  selector: 'app-marvis-detail-dialog',
  standalone: true,
  imports: [
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatExpansionModule,
    StatusBadgeComponent,
  ],
  templateUrl: './marvis-detail-dialog.component.html',
  styleUrl: './marvis-detail-dialog.component.scss',
})
export class MarvisDetailDialogComponent {
  constructor(
    public dialogRef: MatDialogRef<MarvisDetailDialogComponent>,
    @Inject(MAT_DIALOG_DATA) public data: MarvisDetailData,
  ) {}

  formatJson(detail: Record<string, unknown>): string {
    try {
      return JSON.stringify(detail, null, 2);
    } catch {
      return String(detail);
    }
  }

  trackByTest(_idx: number, test: MarvisTest): string {
    return test.test_type;
  }

  close(): void {
    this.dialogRef.close();
  }
}
```

- [ ] **Step 10.2: Template**

Create `marvis-detail-dialog.component.html`:

```html
<div class="mdd-header">
  <div class="mdd-title">
    <div class="mdd-ap">{{ data.ap.ap_name || '(unnamed)' }}</div>
    <div class="mdd-meta">{{ data.ap.switch_name }} · {{ data.ap.switch_port }} · VLAN {{ data.vlan.vlan }}</div>
  </div>
  <button matIconButton (click)="close()" aria-label="Close">
    <mat-icon>close</mat-icon>
  </button>
</div>

<div class="mdd-body">
  @if (data.vlan.has_pcap && data.vlan.pcap_url) {
    <a class="mdd-pcap" [href]="data.vlan.pcap_url" target="_blank" rel="noopener">
      <mat-icon>download</mat-icon>
      Download PCAP
    </a>
  }

  @for (test of data.vlan.tests; track trackByTest($index, test)) {
    <div class="mdd-test">
      <div class="mdd-test-head">
        <span class="mdd-test-type">{{ test.test_type }}</span>
        <app-status-badge [status]="test.status" size="sm" />
      </div>
      <div class="mdd-test-summary">{{ test.summary }}</div>
      <mat-expansion-panel class="mdd-raw">
        <mat-expansion-panel-header>
          <mat-panel-title>Raw response</mat-panel-title>
        </mat-expansion-panel-header>
        <pre class="mdd-json">{{ formatJson(test.detail) }}</pre>
      </mat-expansion-panel>
    </div>
  }
</div>
```

- [ ] **Step 10.3: Styles**

Create `marvis-detail-dialog.component.scss`:

```scss
:host {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--mv-surface, #fff);
}

.mdd-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
  border-bottom: 1px solid var(--mv-border, #e9ecef);
}

.mdd-title {
  min-width: 0;
}

.mdd-ap {
  font-size: 16px;
  font-weight: 600;
  color: var(--mv-text, #212529);
}

.mdd-meta {
  font-size: 12px;
  color: var(--mv-text-muted, #6c757d);
  margin-top: 2px;
}

.mdd-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mdd-pcap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: #fff3bf;
  color: #5c3d00;
  text-decoration: none;
  font-weight: 600;
  font-size: 13px;
  width: fit-content;

  &:hover {
    background: #ffe066;
  }
}

.mdd-test {
  border: 1px solid var(--mv-border, #e9ecef);
  border-radius: 8px;
  padding: 12px 14px;
  background: var(--mv-surface, #fff);
}

.mdd-test-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.mdd-test-type {
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0.5px;
  color: var(--mv-text, #212529);
}

.mdd-test-summary {
  font-size: 13px;
  color: var(--mv-text-muted, #495057);
  margin: 8px 0 0;
}

.mdd-raw {
  margin-top: 10px;
}

.mdd-json {
  font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  font-size: 11px;
  background: var(--mv-surface-2, #f8f9fa);
  padding: 10px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow: auto;
}
```

- [ ] **Step 10.4: Verify build**

```bash
cd /Users/tmunzer/4_dev/mist_validation/frontend
npm run build
```

Expected: clean build.

- [ ] **Step 10.5: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add frontend/src/app/features/report-view/marvis-detail-dialog.component.ts frontend/src/app/features/report-view/marvis-detail-dialog.component.html frontend/src/app/features/report-view/marvis-detail-dialog.component.scss
git commit -m "feat(marvis): add detail dialog component"
```

---

## Task 11 — Frontend: report-view integration (live signal + section render + dialog wire-up)

**Goal:** Wire it all together. The report view subscribes to `marvis_progress`, mounts the matrix, and opens the dialog on cell click.

**Files:**
- Modify: `frontend/src/app/features/report-view/report-view.component.ts`
- Modify: `frontend/src/app/features/report-view/report-view.component.html`

- [ ] **Step 11.1: Add types and live signal in the TS file**

Near the top of `report-view.component.ts`, add to the imports:

```typescript
import { MarvisMatrixComponent, MarvisCellClick, MarvisLiveSnapshot, MarvisResult } from './marvis-matrix.component';
import { MarvisDetailDialogComponent } from './marvis-detail-dialog.component';
```

In the `ReportResult` interface (around line 69-76), add:

```typescript
interface ReportResult {
  site_info: SiteInfo;
  template_variables: TemplateVariable[];
  aps: DeviceResult[];
  switches: SwitchResult[];
  gateways: GatewayResult[];
  marvis_minis?: MarvisResult;
  summary: { pass: number; fail: number; warn: number; info: number };
}
```

Add `MarvisMatrixComponent` to the component's `imports` array (around line 136-147):

```typescript
  imports: [
    DatePipe,
    MatButtonModule,
    MatDialogModule,
    MatExpansionModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    StatusBadgeComponent,
    PageShellComponent,
    RunningScreenComponent,
    MarvisMatrixComponent,
  ],
```

Add a live snapshot signal next to `report = signal<ReportResponse | null>(null);` (line 164):

```typescript
  marvisLive = signal<MarvisLiveSnapshot | null>(null);
```

Add a computed for the matrix's effective data so the template doesn't repeat the fallback expression:

```typescript
  marvisData = computed<MarvisResult | MarvisLiveSnapshot | null>(() => {
    const persisted = this.report()?.result?.marvis_minis;
    if (persisted) return persisted;
    return this.marvisLive();
  });

  marvisIsLive = computed<boolean>(() => {
    return !this.report()?.result?.marvis_minis && this.marvisLive() !== null;
  });
```

In `subscribeWs()` (around line 576-622), after the `report_failed` branch, add:

```typescript
      if (type === 'marvis_progress') {
        const data = msg['data'] as MarvisLiveSnapshot;
        if (data) {
          this.marvisLive.set(data);
        }
      }
```

Add the cell-click handler at the bottom of the class (next to `openDeviceDetail`):

```typescript
  openMarvisDetail(click: MarvisCellClick): void {
    this.dialog.open(MarvisDetailDialogComponent, {
      data: { ap: click.ap, vlan: click.vlan },
      width: '700px',
      maxWidth: '92vw',
      height: '100vh',
      maxHeight: '100vh',
      position: { right: '0', top: '0' },
      panelClass: 'mv-side-drawer',
      autoFocus: false,
    });
  }
```

- [ ] **Step 11.2: Render the section in the template**

In `report-view.component.html`, find a stable insertion point — a good location is right before the closing of the main scorecard area, or after the existing devices section. Search for a sentinel like `<!-- Findings sub-view -->` or the closing of the gateways block. Add this block:

```html
@if (marvisData()) {
  <section class="mv-card marvis-section">
    <div class="mv-card-head">
      <div>
        <h3 class="mv-card-title">Synthetic tests (Marvis Minis)</h3>
        <p class="mv-card-sub">
          @if (marvisIsLive()) {
            Running… {{ ($any(marvisData()).progress | number: '1.0-0') ?? 0 }}%
          } @else if ($any(marvisData()).status === 'completed') {
            {{ $any(marvisData()).summary.pass }} pass · {{ $any(marvisData()).summary.fail }} fail
            · duration {{ $any(marvisData()).duration_seconds }}s
          } @else if ($any(marvisData()).status === 'trigger_failed') {
            <span class="marvis-error">Trigger failed: {{ $any(marvisData()).trigger_error }}</span>
          } @else if ($any(marvisData()).status === 'timeout') {
            Test timed out after {{ $any(marvisData()).duration_seconds }}s
          }
        </p>
      </div>
    </div>
    <div class="marvis-section-body">
      <app-marvis-matrix
        [data]="marvisData()"
        [live]="marvisIsLive()"
        (cellClick)="openMarvisDetail($event)"
      />
    </div>
  </section>
}
```

(Use `$any()` to bypass the union-type narrowing in the template — this is a common pattern in the existing report-view template, see how it handles other shapes. If `$any` produces a lint warning, replace with a typed getter on the component.)

In `report-view.component.scss`, append minimal section styles if missing:

```scss
.marvis-section {
  margin-top: 24px;
}

.marvis-section-body {
  padding: 0;
}

.marvis-error {
  color: #c92a2a;
  font-weight: 600;
}
```

(If `report-view.component.scss` already covers `.mv-card` styling, no change needed besides the error color.)

- [ ] **Step 11.3: Verify build**

```bash
cd /Users/tmunzer/4_dev/mist_validation/frontend
npm run build
```

Expected: clean build. If there's a `Property 'progress' does not exist on type 'MarvisResult'` error in the template, the `$any` narrowing isn't covering it; switch to component-side getters that branch on the union shape.

- [ ] **Step 11.4: End-to-end manual verification (the big one)**

Restart both dev servers. Pick a site with Marvis subscription enabled (or any site to verify the trigger-failed path). On the site selector:

1. Toggle "Marvis Minis" on. Toggle should be visible and not disabled (you have write access).
2. Toggle "Cable Diagnostics" off (to keep wall-clock short).
3. Click Run.
4. Running screen: the "Marvis Minis" step should appear under "Diagnostics", initially `pending`. After ~1 minute (other checks finishing), it should transition to `running` with a message like "Test triggered, waiting for first response…".
5. Open a second browser tab to the report view via the "View report" link in the URL bar. Verify the AP × VLAN matrix renders with spinners in cells, then progressively fills in over ~4 minutes.
6. Click a `pass` cell → drawer opens with 4 sub-tests, each expandable. Confirm DHCP/ARP/DNS/CURL summary strings render, and clicking "Raw response" shows the JSON.
7. Click a `fail` cell (if any) → drawer opens; if `has_pcap`, the "Download PCAP" button is present and clicking it opens a new tab.
8. Click a `not_tested` cell → no drawer opens (inert).
9. After the test completes, the report's overall score should reflect the new pass/fail counts (e.g., scoreValue % drops if there were fails).
10. Open the Network tab → download PDF and CSV. Verify both contain a Marvis section.
11. If the org has no Marvis subscription, expect the matrix to show the trigger error and the report to still complete.

If any step fails, fix the issue before committing.

- [ ] **Step 11.5: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add frontend/src/app/features/report-view/report-view.component.ts frontend/src/app/features/report-view/report-view.component.html frontend/src/app/features/report-view/report-view.component.scss
git commit -m "feat(marvis): wire matrix + dialog into report view with live WS updates"
```

---

## Task 12 — Frontend: validation-reference entry

**Goal:** Document the new check on the validation reference page.

**Files:**
- Modify: `frontend/src/app/features/validation-reference/validation-reference.component.ts`

- [ ] **Step 12.1: Add a new section**

In the `sections: CheckSection[]` array, add a new entry after the existing `Site` section:

```typescript
    {
      category: 'Synthetic tests',
      checks: [
        {
          name: 'Marvis Minis — DHCP',
          scope: 'Per AP × VLAN — opt-in',
          pass: 'DHCP lease obtained',
          warn: '—',
          fail: 'DHCP unresponsive or rejected',
        },
        {
          name: 'Marvis Minis — ARP',
          scope: 'Per AP × VLAN — opt-in',
          pass: 'ARP resolved gateway MAC',
          warn: '—',
          fail: 'ARP timed out or no entry',
        },
        {
          name: 'Marvis Minis — DNS',
          scope: 'Per AP × VLAN — opt-in',
          pass: 'All test URLs resolved',
          warn: '—',
          fail: 'One or more URLs failed to resolve',
        },
        {
          name: 'Marvis Minis — CURL',
          scope: 'Per AP × VLAN — opt-in',
          pass: 'All target URLs returned a 2xx/3xx',
          warn: '—',
          fail: 'One or more URLs unreachable',
        },
      ],
    },
```

- [ ] **Step 12.2: Verify build**

```bash
cd /Users/tmunzer/4_dev/mist_validation/frontend
npm run build
```

Expected: clean build.

- [ ] **Step 12.3: Commit**

```bash
cd /Users/tmunzer/4_dev/mist_validation
git add frontend/src/app/features/validation-reference/validation-reference.component.ts
git commit -m "docs(marvis): add Marvis Minis entries to validation reference"
```

---

## Task 13 — Final manual end-to-end verification

**Goal:** Verify the full feature works against a real Mist org with Marvis subscription.

- [ ] **Step 13.1: Clean local DB to ensure migrations are exercised**

```bash
cd /Users/tmunzer/4_dev/mist_validation/backend
rm -f reports.db
```

- [ ] **Step 13.2: Run dev servers**

```bash
# Terminal A
cd /Users/tmunzer/4_dev/mist_validation/backend
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
# Terminal B
cd /Users/tmunzer/4_dev/mist_validation/frontend
npm start
```

- [ ] **Step 13.3: Walk the happy path**

Login → pick org → pick site (one with at least 2 APs and Marvis subscription) → toggle Marvis Minis on → toggle off cable tests → Run.

Confirm in order:
1. Running screen — "Marvis Minis" step under "Diagnostics" group, ETA includes the marvis budget (~4 min addition).
2. Backend logs — single `marvis_trigger` POST, then ~32 polls with `marvis_poll_*` debug entries.
3. Report view — matrix cells fill in live as polls return; final result shows complete grid; click cells, verify drill-downs.
4. Site score reflects Marvis pass/fail counts (compare to a prior run on the same site without Marvis).
5. PDF export — Marvis section appears, table is correct.
6. CSV export — `marvis_minis.csv` is in the zip with the expected rows.

- [ ] **Step 13.4: Walk the failure path**

If you have a read-only login: try to enable Marvis — the toggle should be disabled with the tooltip "Marvis Minis requires write access to the organization."

If you have a write login on an org without Marvis subscription: enable Marvis and run. After ~5s the step should mark `failed` with the trigger-error message; the rest of the report still completes; the matrix section shows the error message.

- [ ] **Step 13.5: Final commit (if any in-flight fixes)**

If the verification revealed bugs you fixed inline:

```bash
cd /Users/tmunzer/4_dev/mist_validation
git status
git add -p   # interactively stage the fixes
git commit -m "fix(marvis): <whatever you fixed during verification>"
```

If everything works as-is, no commit is needed for this step.

---

## Spec coverage check

Quick map from spec sections to implementation tasks:

| Spec section            | Tasks                |
|-------------------------|----------------------|
| §3.1 marvis_service.py  | 2, 3                 |
| §3.2 validation_service | 4                    |
| §3.3 ETA model          | 4                    |
| §3.4 Pre-flight gate    | 1                    |
| §4.1 Trigger            | 3                    |
| §4.2 Trigger failure    | 3, 4                 |
| §4.3 Poll loop          | 3                    |
| §4.4 Per-poll updates   | 3                    |
| §5 Result schema        | 2 (parser), 3 (orchestration) |
| §5.4 Score impact       | 4                    |
| §6 WS broadcasts        | 3                    |
| §7.1 Site selector toggle | 7                  |
| §7.2 Running screen     | 8                    |
| §7.3 Report view        | 11                   |
| §7.4 Matrix component   | 9, 10                |
| §7.5 PDF export         | 5                    |
| §7.6 CSV export         | 6                    |
| §7.7 Validation reference | 12                 |
| §8 Error handling       | 1, 3, 4, 11          |
