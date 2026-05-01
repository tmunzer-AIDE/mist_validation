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
