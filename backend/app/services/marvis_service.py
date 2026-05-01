"""
Marvis Minis synthetic-test service.

Public entry point: `run_marvis_minis`. Trigger and poll loop are added in a
later task; this module currently exposes the pure parsing + scoring helpers
that convert a raw `test_details` API response into the report schema described
in docs/superpowers/specs/2026-05-01-marvis-minis-design.md §5.
"""

from __future__ import annotations

import logging
import mistapi
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


# ── HTTP wrappers ─────────────────────────────────────────────────────────

async def _trigger_test(session: Any, site_id: str) -> tuple[str | None, str | None]:
    """POST /api/v1/sites/{site_id}/synthetic_test → (test_id, error).

    `mist_post`/`mist_get` are synchronous in the mistapi SDK, so they're
    awaited via `mistapi.arun`, which dispatches them to a thread executor.
    """
    uri = f"/api/v1/sites/{site_id}/synthetic_test"
    try:
        resp = await mistapi.arun(session.mist_post, uri, body={})
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
    uri = f"/api/v1/labs/orgs/{org_id}/synthetic_test"
    query = {
        "q": "test_details",
        "view": "table",
        "site_id": site_id,
        "test_id": test_id,
    }
    try:
        resp = await mistapi.arun(session.mist_get, uri, query=query)
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
