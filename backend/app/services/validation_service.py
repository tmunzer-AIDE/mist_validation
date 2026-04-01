"""
Post-deployment validation service.

Validates a Mist site by checking template variables, AP health,
switch health (including VC and cable tests), and gateway health.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import mistapi
from mistapi.api.v1.orgs import (
    gatewaytemplates as org_gatewaytemplates,
)
from mistapi.api.v1.orgs import (
    networktemplates as org_networktemplates,
)
from mistapi.api.v1.orgs import (
    rftemplates as org_rftemplates,
)
from mistapi.api.v1.orgs import (
    sitegroups as org_sitegroups,
)
from mistapi.api.v1.orgs import (
    sitetemplates as org_sitetemplates,
)
from mistapi.api.v1.sites import (
    devices,
    gatewaytemplates,
    networks,
    setting,
    stats,
    wlans,
)
from mistapi.api.v1.sites import (
    servicepolicies as site_servicepolicies,
)
from mistapi.api.v1.sites import (
    services as site_services,
)

from app.utils.cable_test import clean_terminal_text, parse_tdr_output
from app.utils.variables import extract_variables

logger = logging.getLogger(__name__)

# Step definitions: (id, default_label)
_STEPS = [
    ("site_info", "Site Configuration"),
    ("templates", "Templates & WLANs"),
    ("variables", "Template Variables"),
    ("config_events", "Configuration Events"),
    ("aps", "Access Points"),
    ("switches", "Switches"),
    ("gateways", "Gateways"),
    ("cable_tests", "Cable Tests"),
]


class _ProgressTracker:
    """Manages structured step-based progress and broadcasts via callback."""

    def __init__(self, report_id: str, progress_callback) -> None:
        self.report_id = report_id
        self._callback = progress_callback
        self.steps: dict[str, dict] = {
            sid: {"id": sid, "label": label, "status": "pending", "message": ""} for sid, label in _STEPS
        }
        self.overall_completed = 0
        self.overall_total = 0  # 0 = discovery phase (indeterminate)
        self._persist_counter = 0
        self._persist_lock = asyncio.Lock()

    async def start_step(self, step_id: str, message: str = "") -> None:
        self.steps[step_id]["status"] = "running"
        self.steps[step_id]["message"] = message
        await self._broadcast()

    async def complete_step(self, step_id: str, message: str = "", *, increment: bool = True) -> None:
        self.steps[step_id]["status"] = "completed"
        self.steps[step_id]["message"] = message
        if increment and self.overall_total > 0:
            self.overall_completed += 1
        await self._broadcast()

    async def update_step(self, step_id: str, message: str) -> None:
        """Update message without changing status or incrementing progress."""
        self.steps[step_id]["message"] = message
        await self._broadcast()

    def update_label(self, step_id: str, label: str) -> None:
        """Update the label (e.g., to include device count). No broadcast."""
        self.steps[step_id]["label"] = label

    def set_execution_total(self, cable_test_ports: int) -> None:
        """Switch from discovery (indeterminate) to execution (determinate).

        Execution-phase steps: aps, switches, gateways, cable_tests (N ports).
        If cable_test_ports == 0, cable_tests still counts as 1 step.
        """
        self.overall_total = 3 + max(cable_test_ports, 1)  # aps + switches + gateways + cable tests
        self.overall_completed = 0

    async def complete_cable_test_port(self, message: str) -> None:
        """Increment progress for one completed cable test port."""
        self.steps["cable_tests"]["message"] = message
        self.overall_completed += 1
        self._persist_counter += 1
        await self._broadcast()

    async def _broadcast(self) -> None:
        if self._callback:
            await self._callback(self.report_id, {
                "type": "report_progress",
                "data": {
                    "status": "running",
                    "overall_completed": self.overall_completed,
                    "overall_total": self.overall_total,
                    "steps": list(self.steps.values()),
                },
            })


async def run_post_deployment_validation(
    job_id: str,
    site_id: str,
    cloud_region: str,
    org_id: str,
    include_cable_tests: bool = False,
    progress_callback=None,
    token: str | None = None,
    email: str | None = None,
    password: str | None = None,
) -> None:
    """Run the full post-deployment validation for a site."""
    from app.services.mist_service import MistService
    from app import db

    tracker = _ProgressTracker(job_id, progress_callback)

    try:
        mist = MistService(org_id=org_id, cloud_region=cloud_region, api_token=token, email=email, password=password)
        session = mist.get_session()

        result: dict = {
            "site_info": {},
            "template_variables": [],
            "aps": [],
            "switches": [],
            "gateways": [],
            "summary": {"pass": 0, "fail": 0, "warn": 0},
        }

        # ── Discovery phase (indeterminate progress bar) ─────────────

        # Step 1: Site info & settings
        await tracker.start_step("site_info", "Fetching site configuration...")
        site_setting_resp = await mistapi.arun(setting.getSiteSetting, session, site_id)
        site_vars = site_setting_resp.data.get("vars", {}) if site_setting_resp.status_code == 200 else {}

        site_data = await mist.get_site(site_id)
        site_name = site_data.get("name", site_id)
        await db.update_job(job_id, site_name=site_name, status="running")

        site_address = site_data.get("address", "")
        sitegroup_ids = site_data.get("sitegroup_ids", [])
        sitegroup_names = await _resolve_sitegroup_names(session, mist.org_id, sitegroup_ids)
        await tracker.complete_step("site_info")

        # Step 2: Fetch assigned templates + WLANs
        await tracker.start_step("templates", "Checking templates and WLANs...")
        assigned_templates, assigned_template_data = await _fetch_assigned_templates(session, mist.org_id, site_data)
        derived_sources = await _fetch_derived_sources(session, site_id)
        wlan_info = await _fetch_wlan_info(session, site_id)
        result["site_info"] = {
            "site_name": site_name,
            "site_address": site_address,
            "site_groups": sitegroup_names,
            "templates": assigned_templates,
            "org_wlans": wlan_info["org_wlans"],
            "site_wlans": wlan_info["site_wlans"],
            "device_summary": {},
        }
        wlan_count = len(wlan_info["org_wlans"]) + len(wlan_info["site_wlans"])
        await tracker.complete_step("templates", f"{len(assigned_templates)} templates, {wlan_count} WLANs")

        # Step 3: Template variable validation (synchronous, no API calls)
        await tracker.start_step("variables", "Validating template variables...")
        templates_raw = assigned_template_data + derived_sources
        result["template_variables"] = _validate_template_variables(
            templates_raw, wlan_info["all_wlans_raw"], site_vars
        )
        await tracker.complete_step("variables", f"{len(result['template_variables'])} variables checked")

        # Step 4: Fetch config events for all device types
        await tracker.start_step("config_events", "Fetching device configuration events...")
        config_events = await _fetch_config_events(session, site_id)
        await tracker.complete_step("config_events")

        # ── Execution phase (determinate progress bar) ───────────────

        # Parallel fetch: switch UP ports + switch device stats
        up_ports_by_mac, sw_stats_resp = await asyncio.gather(
            _fetch_switch_up_ports(session, site_id),
            mistapi.arun(stats.listSiteDevicesStats, session, site_id, type="switch", limit=1000),
        )
        switch_stats = sw_stats_resp.data if sw_stats_resp.status_code == 200 else []

        # When cable tests are not requested, treat port count as 0 for progress calculation
        if include_cable_tests:
            total_cable_ports = sum(
                len(up_ports_by_mac.get(sw.get("mac", ""), [])) for sw in switch_stats if sw.get("status") == "connected"
            )
        else:
            total_cable_ports = 0

        tracker.set_execution_total(total_cable_ports)

        # Step 5: AP validation
        await tracker.start_step("aps", "Validating access points...")
        result["aps"] = await _validate_aps(session, site_id, config_events)
        tracker.update_label("aps", f"Access Points ({len(result['aps'])})")
        await tracker.complete_step("aps", f"{len(result['aps'])} APs validated")

        # Step 6: Switch health validation (parallel, no cable tests)
        tracker.update_label("switches", f"Switches ({len(switch_stats)})")
        await tracker.start_step("switches", "Validating switches...")
        result["switches"] = _validate_switch_health(switch_stats, config_events)
        await tracker.complete_step("switches", f"{len(result['switches'])} switches validated")

        # Step 7: Gateway validation
        tracker.update_label("gateways", "Gateways")
        await tracker.start_step("gateways", "Validating gateways...")
        result["gateways"] = await _validate_gateways(session, site_id, config_events, site_vars)
        tracker.update_label("gateways", f"Gateways ({len(result['gateways'])})")
        await tracker.complete_step("gateways", f"{len(result['gateways'])} gateways validated")

        # Step 8: Cable tests (opt-in)
        if include_cable_tests and total_cable_ports > 0:
            tracker.update_label("cable_tests", f"Cable Tests ({total_cable_ports} ports)")
            await tracker.start_step("cable_tests", f"Testing {total_cable_ports} ports...")
            await _run_all_cable_tests(session, site_id, result["switches"], up_ports_by_mac, tracker)
            # increment=False: ports already counted individually via complete_cable_test_port
            await tracker.complete_step("cable_tests", f"{total_cable_ports} ports tested", increment=False)
        else:
            msg = "Skipped (opt-in)" if not include_cable_tests else "No cable test ports"
            await tracker.start_step("cable_tests", msg)
            await tracker.complete_step("cable_tests", msg)

        # Compute device summary and overall summary
        result["site_info"]["device_summary"] = _compute_device_summary(result)
        result["summary"] = _compute_summary(result)

        # Save results
        await db.update_job(
            job_id,
            result=result,
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        if progress_callback:
            await progress_callback(job_id, {"type": "report_complete", "data": {"status": "completed", "report_id": job_id}})

        logger.info("validation_report_completed job_id=%s site_id=%s", job_id, site_id)

    except Exception as e:
        logger.error("validation_report_failed job_id=%s error=%s", job_id, str(e), exc_info=True)
        await db.update_job(job_id, status="failed", error="Validation failed")

        if progress_callback:
            await progress_callback(
                job_id,
                {
                    "type": "report_complete",
                    "data": {"status": "failed", "report_id": job_id, "error": "Validation failed"},
                },
            )


def _device_conn_status(device_status: str) -> str:
    """Map Mist device status to a check status."""
    if device_status == "connected":
        return "pass"
    if device_status in ("upgrading", "restarting"):
        return "warn"
    return "fail"


def _name_check(name: str) -> dict:
    """Build a name_defined check dict."""
    return {
        "check": "name_defined",
        "status": "pass" if name and name.strip() else "fail",
        "value": name or "(not set)",
    }


def _firmware_check(device: dict) -> dict:
    """Build a firmware_version check dict."""
    fw = device.get("version", device.get("fw_version", ""))
    return {"check": "firmware_version", "status": "info", "value": fw or "unknown"}


def _extract_jinja2_vars(data: object) -> set[str]:
    """Recursively scan all string values in a data structure for Jinja2 variables."""
    found: set[str] = set()
    if isinstance(data, str):
        found.update(extract_variables(data))
    elif isinstance(data, dict):
        for v in data.values():
            found.update(_extract_jinja2_vars(v))
    elif isinstance(data, list):
        for item in data:
            found.update(_extract_jinja2_vars(item))
    return found


# ── Site group resolution ────────────────────────────────────────────────


async def _resolve_sitegroup_names(session, org_id: str, sitegroup_ids: list[str]) -> list[str]:
    """Resolve site group UUIDs to names by fetching all org site groups."""
    if not sitegroup_ids:
        return []
    try:
        resp = await mistapi.arun(org_sitegroups.listOrgSiteGroups, session, org_id, limit=1000)
        if resp.status_code != 200:
            return [gid[:8] for gid in sitegroup_ids]
        id_to_name = {g["id"]: g.get("name", g["id"][:8]) for g in resp.data if isinstance(g, dict)}
        return [id_to_name.get(gid, gid[:8]) for gid in sitegroup_ids]
    except Exception as e:
        logger.warning("sitegroup_resolve_error error=%s", str(e))
        return [gid[:8] for gid in sitegroup_ids]


# ── Template & WLAN fetching ─────────────────────────────────────────────


_DERIVED_SOURCES = [
    ("network", networks.listSiteNetworksDerived, {}),
    ("application", site_services.listSiteServicesDerived, {}),
    ("application_policy", site_servicepolicies.listSiteServicePoliciesDerived, {}),
]

# Mapping from site_data field → (display type, org-level getter)
_ASSIGNED_TEMPLATE_FIELDS = [
    ("rftemplate_id", "rf_template", org_rftemplates.getOrgRfTemplate),
    ("networktemplate_id", "network_template", org_networktemplates.getOrgNetworkTemplate),
    ("gatewaytemplate_id", "gateway_template", org_gatewaytemplates.getOrgGatewayTemplate),
    ("sitetemplate_id", "site_template", org_sitetemplates.getOrgSiteTemplate),
]


async def _fetch_one_assigned_template(
    session, org_id: str, field: str, tmpl_type: str, api_fn, tmpl_id: str
) -> tuple[str, str, str, dict | None]:
    """Fetch a single assigned template. Returns (field, tmpl_type, tmpl_id, data_or_None)."""
    try:
        resp = await mistapi.arun(api_fn, session, org_id, tmpl_id)
        if resp.status_code == 200 and isinstance(resp.data, dict):
            return field, tmpl_type, tmpl_id, resp.data
    except Exception as e:
        logger.warning("assigned_template_fetch_error tmpl_type=%s tmpl_id=%s error=%s", tmpl_type, tmpl_id, str(e))
    return field, tmpl_type, tmpl_id, None


async def _fetch_assigned_templates(
    session, org_id: str, site_data: dict
) -> tuple[list[dict], list[tuple[str, list[dict]]]]:
    """Fetch assigned templates by ID from site info.

    Fetches all assigned templates in parallel via ``asyncio.gather()``.

    Returns:
        (site_info_entries, template_data_for_var_scan)
        - site_info_entries: list of {"type", "name", "id"} for site_info display
        - template_data_for_var_scan: list of (template_type, [full_template_dict]) for variable extraction
    """
    # Build tasks for all assigned templates
    tasks = []
    for field, tmpl_type, api_fn in _ASSIGNED_TEMPLATE_FIELDS:
        tmpl_id = site_data.get(field)
        if tmpl_id:
            tasks.append(_fetch_one_assigned_template(session, org_id, field, tmpl_type, api_fn, tmpl_id))

    if not tasks:
        return [], []

    results = await asyncio.gather(*tasks, return_exceptions=True)

    site_info_entries: list[dict] = []
    var_scan_data: list[tuple[str, list[dict]]] = []

    for result in results:
        if isinstance(result, Exception):
            logger.warning("assigned_template_fetch_error error=%s", str(result))
            continue
        _field, tmpl_type, tmpl_id, data = result
        if data:
            site_info_entries.append({"type": tmpl_type, "name": data.get("name", tmpl_id[:8]), "id": tmpl_id})
            var_scan_data.append((tmpl_type, [data]))
        else:
            site_info_entries.append({"type": tmpl_type, "name": tmpl_id[:8], "id": tmpl_id})

    return site_info_entries, var_scan_data


async def _fetch_one_template(
    session, site_id: str, template_type: str, api_fn, extra_kwargs: dict
) -> tuple[str, list[dict]] | None:
    """Fetch a single derived source type, returning None on failure."""
    try:
        resp = await mistapi.arun(api_fn, session, site_id, **extra_kwargs)
        if resp.status_code != 200:
            logger.warning("template_fetch_failed template_type=%s status=%s", template_type, resp.status_code)
            return None
        templates_data = resp.data if isinstance(resp.data, list) else [resp.data]
        valid = [t for t in templates_data if isinstance(t, dict)]
        return (template_type, valid)
    except Exception as e:
        logger.warning("template_fetch_error template_type=%s error=%s", template_type, str(e))
        return None


async def _fetch_derived_sources(session, site_id: str) -> list[tuple[str, list[dict]]]:
    """Fetch derived objects (networks, services, service policies) for variable scanning."""
    results = await asyncio.gather(
        *[_fetch_one_template(session, site_id, ttype, fn, kw) for ttype, fn, kw in _DERIVED_SOURCES]
    )
    return [r for r in results if r is not None]


async def _fetch_wlan_info(session, site_id: str) -> dict:
    """Fetch derived WLANs and split into org WLANs (from templates) and site WLANs.

    Returns dict with keys: org_wlans, site_wlans, all_wlans_raw.
    """
    org_wlans: list[dict] = []
    site_wlans: list[dict] = []
    all_raw: list[dict] = []

    try:
        resp = await mistapi.arun(wlans.listSiteWlansDerived, session, site_id, resolve=True)
        if resp.status_code == 200:
            all_raw = resp.data if isinstance(resp.data, list) else [resp.data]
            for w in all_raw:
                if not isinstance(w, dict):
                    continue
                ssid = w.get("ssid", w.get("name", ""))
                if w.get("template_id"):
                    org_wlans.append({"ssid": ssid, "template_id": w["template_id"]})
                else:
                    site_wlans.append({"ssid": ssid})
    except Exception as e:
        logger.warning("wlan_fetch_error error=%s", str(e))

    return {"org_wlans": org_wlans, "site_wlans": site_wlans, "all_wlans_raw": all_raw}


def _validate_template_variables(
    templates_raw: list[tuple[str, list[dict]]],
    wlans_raw: list[dict],
    site_vars: dict,
) -> list[dict]:
    """Check that Jinja2 variables found in templates are defined in site vars."""
    results: list[dict] = []
    var_keys = set(site_vars.keys()) if site_vars else set()

    # Scan non-WLAN templates
    for template_type, template_list in templates_raw:
        for tmpl in template_list:
            tmpl_name = tmpl.get("name", template_type)
            for var_name in sorted(_extract_jinja2_vars(tmpl)):
                root_var = var_name.split(".")[0]
                defined = root_var in var_keys
                results.append(
                    {
                        "template_type": template_type,
                        "template_name": tmpl_name,
                        "variable": var_name,
                        "value": site_vars.get(root_var, ""),
                        "defined": defined,
                        "status": "pass" if defined else "fail",
                    }
                )

    # Scan WLANs
    for w in wlans_raw:
        if not isinstance(w, dict):
            continue
        wlan_name = w.get("ssid", w.get("name", "wlan"))
        for var_name in sorted(_extract_jinja2_vars(w)):
            root_var = var_name.split(".")[0]
            defined = root_var in var_keys
            results.append(
                {
                    "template_type": "wlan",
                    "template_name": wlan_name,
                    "variable": var_name,
                    "value": site_vars.get(root_var, ""),
                    "defined": defined,
                    "status": "pass" if defined else "fail",
                }
            )

    return results


# ── Config events ────────────────────────────────────────────────────────

_CONFIG_EVENT_PREFIXES = ("AP_CONFIG", "SW_CONFIG", "GW_CONFIG")


async def _fetch_config_events(session, site_id: str) -> dict[str, dict]:
    """Fetch recent system events and return the latest config event per device MAC.

    Returns:
        dict mapping device MAC → {"type": event_type, "timestamp": ..., "status": "pass"|"fail"}
    """
    latest_by_mac: dict[str, dict] = {}

    try:
        resp = await mistapi.arun(
            devices.searchSiteDeviceEvents,
            session,
            site_id,
            type="AP_CONFIG*,SW_CONFIG*,GW_CONFIG*",
            limit=1000,
            duration="24h",
        )
        if resp.status_code != 200:
            logger.warning("config_events_fetch_failed status=%s", resp.status_code)
            return latest_by_mac

        all_events = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        if not isinstance(all_events, list):
            logger.debug("config_events_unexpected_format data_type=%s", type(resp.data).__name__)
            return latest_by_mac

        logger.debug("config_events_fetched count=%d", len(all_events))

        for ev in all_events:
            if not isinstance(ev, dict):
                continue
            ev_type = ev.get("type", "")
            if not any(ev_type.startswith(prefix) for prefix in _CONFIG_EVENT_PREFIXES):
                continue

            mac = ev.get("mac", ev.get("device_mac", ""))
            if not mac:
                continue

            timestamp = ev.get("timestamp", 0)
            existing = latest_by_mac.get(mac)
            if existing and existing["timestamp"] >= timestamp:
                continue

            is_success = ev_type.endswith("_CONFIGURED") or ev_type.endswith("_CONFIG_CHANGED_BY_USER")
            latest_by_mac[mac] = {
                "type": ev_type,
                "timestamp": timestamp,
                "status": "pass" if is_success else "fail",
            }
    except Exception as e:
        logger.warning("config_events_fetch_error error=%s", str(e))

    return latest_by_mac


def _add_config_status_check(checks: list[dict], mac: str, config_events: dict[str, dict]) -> None:
    """Append a config_status check to the checks list based on the device's latest config event."""
    event = config_events.get(mac)
    if event:
        checks.append(
            {
                "check": "config_status",
                "status": event["status"],
                "value": event["type"],
            }
        )
    else:
        checks.append(
            {
                "check": "config_status",
                "status": "info",
                "value": "No config event found",
            }
        )


# ── AP validation ────────────────────────────────────────────────────────


async def _validate_aps(session, site_id: str, config_events: dict[str, dict]) -> list[dict]:
    """Validate all APs at the site."""
    resp = await mistapi.arun(stats.listSiteDevicesStats, session, site_id, type="ap", limit=1000)
    if resp.status_code != 200:
        logger.warning("ap_stats_fetch_failed status=%s", resp.status_code)
        return []

    results: list[dict] = []
    for ap in resp.data:
        checks: list[dict] = []
        mac = ap.get("mac", "")

        # Name check
        name = ap.get("name", "")
        checks.append(_name_check(name))

        # Firmware version
        checks.append(_firmware_check(ap))

        # Eth0 port speed
        port_stat = ap.get("port_stat", {})
        eth0 = port_stat.get("eth0", {})
        eth0_speed = eth0.get("speed", 0)
        if not eth0:
            speed_status = "info"
            speed_value = "N/A"
        elif eth0_speed >= 1000:
            speed_status = "pass"
            speed_value = f"{eth0_speed} Mbps"
        else:
            speed_status = "warn"
            speed_value = f"{eth0_speed} Mbps"
        checks.append({"check": "eth0_port_speed", "status": speed_status, "value": speed_value})

        # Power constraint
        power_constrained = bool(ap.get("power_constrained", False))
        checks.append(
            {
                "check": "power_constrained",
                "status": "warn" if power_constrained else "pass",
                "value": "Yes" if power_constrained else "No",
            }
        )

        # Connection status
        ap_status = ap.get("status", "unknown")
        checks.append({"check": "connection_status", "status": _device_conn_status(ap_status), "value": ap_status})

        # Config status
        _add_config_status_check(checks, mac, config_events)

        # LLDP neighbor — field may be "lldp_stat" or "lldp_stats"
        lldp_stats = ap.get("lldp_stats", {})
        if lldp_stats and lldp_stats.get("eth0"):
            lldp_eth0 = lldp_stats.get("eth0", {})
        else:
            lldp_eth0 = ap.get("lldp_stat", {})
        if not lldp_eth0:
            logger.debug(
                "ap_lldp_debug mac=%s lldp_keys=%s power_constrained=%s",
                mac,
                list(lldp_stats.keys()) if isinstance(lldp_stats, dict) else type(lldp_stats).__name__,
                ap.get("power_constrained"),
            )
        lldp_neighbor = {
            "system_name": lldp_eth0.get("system_name", ""),
            "port_desc": lldp_eth0.get("port_desc", ""),
        }

        results.append(
            {
                "device_id": ap.get("id", ""),
                "name": name or "(unnamed)",
                "mac": mac,
                "model": ap.get("model", ""),
                "checks": checks,
                "lldp_neighbor": lldp_neighbor,
            }
        )

    return results


# ── Switch validation ────────────────────────────────────────────────────


# Physical copper interface prefixes eligible for cable tests
_COPPER_PORT_PREFIXES = ("ge-", "mge-", "nge-")


def _is_copper_port(port_id: str) -> bool:
    """Check if a port ID is a physical copper Ethernet interface."""
    return port_id.startswith(_COPPER_PORT_PREFIXES)


async def _fetch_switch_up_ports(session, site_id: str) -> dict[str, list[dict]]:
    """Fetch UP copper ports for all switches using the dedicated port search API.

    Only includes physical copper interfaces (ge-*, mge-*, nge-*) suitable for cable tests.
    Returns a dict mapping device MAC → list of port dicts with LLDP info.
    """
    ports_by_mac: dict[str, list[dict]] = {}
    try:
        resp = await mistapi.arun(
            stats.searchSiteSwOrGwPorts,
            session,
            site_id,
            device_type="switch",
            up=True,
            limit=1000,
        )
        if resp.status_code != 200:
            logger.warning("switch_ports_fetch_failed status=%s", resp.status_code)
            return ports_by_mac

        results = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        if not isinstance(results, list):
            return ports_by_mac

        for port in results:
            if not isinstance(port, dict):
                continue
            device_mac = port.get("mac", "")
            port_id = port.get("port_id", "")
            if device_mac and port_id and _is_copper_port(port_id):
                ports_by_mac.setdefault(device_mac, []).append(
                    {
                        "port_id": port_id,
                        "neighbor_system_name": port.get("neighbor_system_name", ""),
                        "neighbor_port_desc": port.get("neighbor_port_desc", ""),
                    }
                )
    except Exception as e:
        logger.warning("switch_ports_fetch_error error=%s", str(e))

    return ports_by_mac


def _validate_switch_health(switch_stats: list[dict], config_events: dict[str, dict]) -> list[dict]:
    """Validate all switches (health only, no cable tests). Synchronous — no API calls needed."""
    return [_validate_single_switch(sw, config_events) for sw in switch_stats]


def _validate_single_switch(sw: dict, config_events: dict[str, dict]) -> dict:
    """Validate a single switch: name, firmware, status, VC. No cable tests."""
    checks: list[dict] = []
    device_id = sw.get("id", "")
    name = sw.get("name", "")
    mac = sw.get("mac", "")

    checks.append(_name_check(name))
    firmware = sw.get("version", sw.get("fw_version", ""))
    checks.append(_firmware_check(sw))

    sw_status = sw.get("status", "unknown")
    checks.append({"check": "connection_status", "status": _device_conn_status(sw_status), "value": sw_status})
    _add_config_status_check(checks, mac, config_events)

    # Virtual chassis check — use module_stat from device stats (no extra API call)
    vc_result = None
    module_stat = sw.get("module_stat", [])
    if sw.get("vc_mac") or (isinstance(module_stat, list) and len(module_stat) > 1):
        vc_result = _check_virtual_chassis_from_stats(module_stat, firmware)

    return {
        "device_id": device_id,
        "name": name or "(unnamed)",
        "mac": mac,
        "model": sw.get("model", ""),
        "checks": checks,
        "virtual_chassis": vc_result,
        "cable_tests": [],
    }


async def _run_all_cable_tests(
    session,
    site_id: str,
    switch_results: list[dict],
    up_ports_by_mac: dict[str, list[dict]],
    tracker: _ProgressTracker,
) -> None:
    """Run cable tests in parallel across switches (sequential per switch) with progress updates."""

    async def _test_one_switch(sw_result: dict) -> None:
        mac = sw_result["mac"]
        up_ports = up_ports_by_mac.get(mac, [])
        if not up_ports:
            return
        name = sw_result["name"]
        device_id = sw_result["device_id"]
        cable_tests: list[dict] = []
        # Sequential within each switch (cable test needs previous to finish)
        for idx, port_info in enumerate(up_ports):
            port_id = port_info["port_id"]
            test_result = await _run_cable_test(session, site_id, device_id, port_id)
            test_result["neighbor_system_name"] = port_info.get("neighbor_system_name", "")
            test_result["neighbor_port_desc"] = port_info.get("neighbor_port_desc", "")
            cable_tests.append(test_result)
            await tracker.complete_cable_test_port(f"{name}: tested {port_id} ({idx + 1}/{len(up_ports)})")
        sw_result["cable_tests"] = cable_tests

    await asyncio.gather(*[_test_one_switch(sw) for sw in switch_results])


def _check_virtual_chassis_from_stats(module_stat: list, expected_firmware: str) -> dict:
    """Check virtual chassis members using module_stat from device stats."""
    if not isinstance(module_stat, list):
        return {"status": "error", "message": "No module_stat data", "members": []}

    members: list[dict] = []
    for idx, mod in enumerate(module_stat):
        if not isinstance(mod, dict):
            continue

        member_fw = mod.get("version", mod.get("fw_version", ""))
        vc_role = mod.get("vc_role", "")
        member_status = mod.get("status", "")

        # Count UP vc_links
        vc_links = mod.get("vc_links", [])
        vc_ports_up = 0
        if isinstance(vc_links, list):
            vc_ports_up = sum(1 for link in vc_links if isinstance(link, dict))

        is_present = member_status != "not-present" and (member_status or vc_role)
        display_status = vc_role or member_status or "unknown"

        member_checks: list[dict] = []
        fw_match = bool(member_fw and member_fw == expected_firmware)
        member_checks.append(
            {
                "check": "firmware_match",
                "status": "pass" if fw_match else ("fail" if member_fw else "info"),
                "value": member_fw or "unknown",
                "expected": expected_firmware,
            }
        )
        member_checks.append(
            {
                "check": "vc_ports_up",
                "status": "pass" if vc_ports_up >= 2 else "fail",
                "value": vc_ports_up,
                "expected": 2,
            }
        )
        member_checks.append(
            {
                "check": "member_present",
                "status": "pass" if is_present else "fail",
                "value": display_status,
            }
        )

        members.append(
            {
                "member_id": mod.get("vc_member", idx),
                "mac": mod.get("mac", ""),
                "serial": mod.get("serial", ""),
                "model": mod.get("model", ""),
                "firmware": member_fw,
                "status": display_status,
                "vc_ports_up": vc_ports_up,
                "checks": member_checks,
            }
        )

    return {"status": "checked", "members": members}


async def _run_cable_test(session, site_id: str, device_id: str, port_id: str) -> dict:
    """Run a cable test on a single switch port using mistapi device_utils."""
    try:
        from mistapi.device_utils.ex import cableTest

        # cableTest is synchronous/threaded — run in a thread to avoid blocking the event loop
        util_response = await asyncio.to_thread(
            lambda: cableTest(session, site_id, device_id, port_id, timeout=15).wait(timeout=20)
        )

        # Check trigger response
        trigger_resp = util_response.trigger_api_response
        if trigger_resp and trigger_resp.status_code not in (200, 201):
            return {
                "port": port_id,
                "status": "error",
                "message": f"Trigger API returned {trigger_resp.status_code}",
                "pairs": [],
            }

        # Use ws_raw_events (unprocessed) instead of ws_data (VT100-processed)
        # to avoid data loss from screen buffer rendering
        raw_events = util_response.ws_raw_events or []
        raw_texts = _extract_raw_texts(raw_events)

        result = _parse_cable_test_results(port_id, raw_texts)
        logger.debug(
            "cable_test_parsed device_id=%s port=%s status=%s pairs=%s",
            device_id, port_id, result["status"], result["pairs"]
        )
        return result

    except Exception as e:
        logger.warning("cable_test_failed device_id=%s port=%s error=%s", device_id, port_id, str(e))
        return {"port": port_id, "status": "error", "message": "Cable test failed", "pairs": []}


def _extract_raw_texts(raw_events: list) -> list[str]:
    """Extract raw text from WebSocket raw events, stripping ANSI escape sequences."""
    texts: list[str] = []
    for event in raw_events:
        raw_str = _dig_raw(event)
        if raw_str and isinstance(raw_str, str):
            cleaned = clean_terminal_text(raw_str)
            if cleaned:
                texts.append(cleaned)
    return texts


def _dig_raw(obj) -> str | None:
    """Recursively dig into a WS event to find the 'raw' text field."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except (ValueError, TypeError):
            return obj

    if isinstance(obj, dict):
        if "raw" in obj:
            return obj["raw"]
        if "data" in obj:
            return _dig_raw(obj["data"])
    return None


def _parse_cable_test_results(port_id: str, raw_messages: list) -> dict:
    """Parse cable test results from WebSocket raw messages using the shared TDR parser.

    Messages are concatenated directly (NO separator) since the terminal
    output already contains newlines.
    """
    full_text = "".join(msg for msg in raw_messages if isinstance(msg, str))

    if not full_text.strip():
        return {"port": port_id, "status": "info", "pairs": [], "raw": [str(m) for m in raw_messages]}

    result = parse_tdr_output(full_text, port_id)
    return {
        "port": port_id,
        "status": result["status"],
        "pairs": result["pairs"],
        "raw": [str(m) for m in raw_messages] if not result["pairs"] else [],
    }


# ── Gateway validation ───────────────────────────────────────────────────


async def _validate_gateways(session, site_id: str, config_events: dict[str, dict], site_vars: dict) -> list[dict]:
    """Validate all gateways at the site with port classification and network details."""
    resp = await mistapi.arun(stats.listSiteDevicesStats, session, site_id, type="gateway", limit=1000)
    if resp.status_code != 200:
        logger.warning("gateway_stats_fetch_failed status=%s", resp.status_code)
        return []

    # Fetch gateway template derived config (base config for all gateways at this site)
    gw_template_config: dict = {}
    try:
        tmpl_resp = await mistapi.arun(gatewaytemplates.listSiteGatewayTemplatesDerived, session, site_id, resolve=True)
        if tmpl_resp.status_code == 200:
            tmpl_data = tmpl_resp.data
            if isinstance(tmpl_data, list) and tmpl_data:
                gw_template_config = tmpl_data[0] if isinstance(tmpl_data[0], dict) else {}
            elif isinstance(tmpl_data, dict):
                gw_template_config = tmpl_data
    except Exception as e:
        logger.warning("gateway_template_fetch_failed error=%s", str(e))

    # Pre-fetch all device configs and port stats in parallel (avoids N+1 per gateway)
    device_config_map: dict[str, dict] = {}
    port_stats_by_mac: dict[str, dict[str, dict]] = {}

    async def _fetch_device_configs():
        try:
            dev_resp = await mistapi.arun(devices.listSiteDevices, session, site_id, type="gateway", limit=1000)
            if dev_resp.status_code == 200 and isinstance(dev_resp.data, list):
                for d in dev_resp.data:
                    if isinstance(d, dict) and d.get("id"):
                        device_config_map[d["id"]] = d
        except Exception as e:
            logger.warning("gateway_device_configs_batch_failed error=%s", str(e))

    async def _fetch_port_stats():
        try:
            port_resp = await mistapi.arun(
                stats.searchSiteSwOrGwPorts,
                session,
                site_id,
                device_type="gateway",
                limit=1000,
            )
            if port_resp.status_code == 200:
                port_results = (
                    port_resp.data.get("results", port_resp.data)
                    if isinstance(port_resp.data, dict)
                    else port_resp.data
                )
                if isinstance(port_results, list):
                    for p in port_results:
                        if isinstance(p, dict) and p.get("mac") and p.get("port_id"):
                            port_stats_by_mac.setdefault(p["mac"], {})[p["port_id"]] = p
        except Exception as e:
            logger.warning("gateway_port_stats_batch_failed error=%s", str(e))

    await asyncio.gather(_fetch_device_configs(), _fetch_port_stats())

    results = await asyncio.gather(
        *[
            _validate_single_gateway(
                gw_stat,
                gw_template_config,
                device_config_map.get(gw_stat.get("id", ""), {}),
                port_stats_by_mac.get(gw_stat.get("mac", ""), {}),
                config_events,
                site_vars,
            )
            for gw_stat in resp.data
        ]
    )
    return list(results)


async def _validate_single_gateway(
    gw_stat: dict,
    gw_template_config: dict,
    device_config: dict,
    port_stats_map: dict[str, dict],
    config_events: dict[str, dict],
    site_vars: dict,
) -> dict:
    """Validate a single gateway: name, firmware, status, ports, networks."""
    device_id = gw_stat.get("id", "")
    name = gw_stat.get("name", "")
    mac = gw_stat.get("mac", "")

    # Merge: device-level overrides template-level (shallow merge per config section)
    port_config = {**gw_template_config.get("port_config", {}), **device_config.get("port_config", {})}
    ip_configs = {**gw_template_config.get("ip_configs", {}), **device_config.get("ip_configs", {})}
    dhcpd_config = {**gw_template_config.get("dhcpd_config", {}), **device_config.get("dhcpd_config", {})}
    networks_list = device_config.get("networks", gw_template_config.get("networks", []))

    # Basic checks
    checks: list[dict] = []
    checks.append(_name_check(name))
    checks.append(_firmware_check(gw_stat))

    gw_status = gw_stat.get("status", "unknown")
    checks.append({"check": "connection_status", "status": _device_conn_status(gw_status), "value": gw_status})

    _add_config_status_check(checks, mac, config_events)

    # Classify ports using port_config
    wan_ports: list[dict] = []
    lan_ports: list[dict] = []

    for iface, cfg in port_config.items():
        if not isinstance(cfg, dict):
            continue
        usage = cfg.get("usage", "")
        pstat = port_stats_map.get(iface, {})
        is_up = pstat.get("up", False)
        neighbor_sys = pstat.get("neighbor_system_name", "")
        neighbor_port = pstat.get("neighbor_port_desc", "")

        if usage == "wan":
            wan_ports.append(
                {
                    "interface": iface,
                    "name": cfg.get("name", ""),
                    "up": is_up,
                    "wan_type": cfg.get("wan_type", cfg.get("wan_source", "")),
                    "neighbor_system_name": neighbor_sys,
                    "neighbor_port_desc": neighbor_port,
                }
            )
        else:
            lan_ports.append(
                {
                    "interface": iface,
                    "network": usage,
                    "up": is_up,
                    "neighbor_system_name": neighbor_sys,
                    "neighbor_port_desc": neighbor_port,
                }
            )

    # WAN/LAN port checks
    wan_up = sum(1 for p in wan_ports if p["up"])
    if not wan_ports:
        wan_status = "info"
    elif all(p["up"] for p in wan_ports):
        wan_status = "pass"
    elif any(p["up"] for p in wan_ports):
        wan_status = "warn"
    else:
        wan_status = "fail"
    checks.append({"check": "wan_port_status", "status": wan_status, "value": f"{wan_up}/{len(wan_ports)} UP"})

    lan_up = sum(1 for p in lan_ports if p["up"])
    if not lan_ports:
        lan_status = "info"
    elif all(p["up"] for p in lan_ports):
        lan_status = "pass"
    elif any(p["up"] for p in lan_ports):
        lan_status = "warn"
    else:
        lan_status = "fail"
    checks.append({"check": "lan_port_status", "status": lan_status, "value": f"{lan_up}/{len(lan_ports)} UP"})

    # Networks with IP and DHCP details
    gw_networks: list[dict] = _build_network_details(ip_configs, dhcpd_config, networks_list, site_vars)

    return {
        "device_id": device_id,
        "name": name or "(unnamed)",
        "mac": mac,
        "model": gw_stat.get("model", ""),
        "checks": checks,
        "wan_ports": wan_ports,
        "lan_ports": lan_ports,
        "networks": gw_networks,
    }


_jinja_env = None


def _get_jinja_env():
    """Get or create shared Jinja2 SandboxedEnvironment (module-level singleton)."""
    global _jinja_env
    if _jinja_env is None:
        from app.utils.variables import create_jinja_env

        _jinja_env = create_jinja_env()
    return _jinja_env


def _resolve_vars(text: str, site_vars: dict) -> str:
    """Resolve Jinja2 variables in a string using site vars."""
    if not text or "{{" not in text:
        return text
    try:
        return _get_jinja_env().from_string(text).render(site_vars)
    except Exception:
        return text


def _build_network_details(ip_configs: dict, dhcpd_config: dict, networks_list: list, site_vars: dict) -> list[dict]:
    """Build network details (IP, DHCP) for a gateway."""
    # Collect network names from ip_configs keys and networks list
    network_names: set[str] = set(ip_configs.keys())
    for net in networks_list:
        if isinstance(net, dict) and net.get("name"):
            network_names.add(net["name"])

    results: list[dict] = []
    for net_name in sorted(network_names):
        ip_cfg = ip_configs.get(net_name, {})
        if not isinstance(ip_cfg, dict):
            ip_cfg = {}
        dhcp_cfg = dhcpd_config.get(net_name, {})
        if not isinstance(dhcp_cfg, dict):
            dhcp_cfg = {}

        # Gateway IP — resolve variables if present
        raw_ip = ip_cfg.get("ip", "")
        netmask = ip_cfg.get("netmask", ip_cfg.get("prefix_length", ""))
        gateway_ip = _resolve_vars(raw_ip, site_vars)
        if gateway_ip and netmask:
            # netmask can be dotted (255.255.255.0), CIDR prefix (/24 or 24), or empty
            netmask_str = str(netmask).lstrip("/")
            if "." in netmask_str:
                # Convert dotted netmask to CIDR
                cidr = sum(bin(int(x)).count("1") for x in netmask_str.split("."))
                gateway_ip = f"{gateway_ip}/{cidr}"
            else:
                gateway_ip = f"{gateway_ip}/{netmask_str}"

        # DHCP status
        dhcp_enabled = dhcp_cfg.get("enabled", True) if dhcp_cfg else False
        dhcp_type = dhcp_cfg.get("type", "server")
        if not dhcp_cfg or not dhcp_enabled:
            dhcp_status = "Disabled"
            dhcp_pool = ""
            dhcp_relay_servers: list[str] = []
        elif dhcp_type == "relay":
            dhcp_status = "Relay"
            dhcp_pool = ""
            dhcp_relay_servers = dhcp_cfg.get("servers", [])
        else:  # "server", "local", or any other value = DHCP server
            dhcp_status = "Server"
            ip_start = dhcp_cfg.get("ip_start", "")
            ip_end = dhcp_cfg.get("ip_end", "")
            dhcp_pool = f"{ip_start} - {ip_end}" if ip_start and ip_end else ""
            dhcp_relay_servers = []

        results.append(
            {
                "name": net_name,
                "gateway_ip": gateway_ip,
                "dhcp_status": dhcp_status,
                "dhcp_pool": dhcp_pool,
                "dhcp_relay_servers": dhcp_relay_servers,
            }
        )

    return results


# ── Summary ──────────────────────────────────────────────────────────────


def _compute_device_summary(result: dict) -> dict:
    """Compute per-device-type counts: total and number with at least one failed check."""
    summary: dict[str, dict] = {}
    for device_type in ("aps", "switches", "gateways"):
        devices_list = result.get(device_type, [])
        total = len(devices_list)
        failed = 0
        for dev in devices_list:
            has_failure = any(c.get("status") == "fail" for c in dev.get("checks", []))
            # Also check cable test failures for switches
            if not has_failure and device_type == "switches":
                has_failure = any(ct.get("status") == "fail" for ct in dev.get("cable_tests", []))
            # Also check VC member failures
            if not has_failure:
                vc = dev.get("virtual_chassis")
                if vc and isinstance(vc, dict):
                    for member in vc.get("members", []):
                        if any(c.get("status") == "fail" for c in member.get("checks", [])):
                            has_failure = True
                            break
            if has_failure:
                failed += 1
        summary[device_type] = {"total": total, "failed": failed}
    return summary


def _compute_summary(result: dict) -> dict:
    """Compute pass/fail/warn counts from all checks."""
    counts = {"pass": 0, "fail": 0, "warn": 0, "info": 0}

    # Template variables
    for item in result.get("template_variables", []):
        s = item.get("status", "info")
        if s in counts:
            counts[s] += 1

    # Device checks (APs, switches, gateways)
    for device_type in ("aps", "switches", "gateways"):
        for device in result.get(device_type, []):
            for check in device.get("checks", []):
                s = check.get("status", "info")
                if s in counts:
                    counts[s] += 1
            # VC member checks
            vc = device.get("virtual_chassis")
            if vc and isinstance(vc, dict):
                for member in vc.get("members", []):
                    for check in member.get("checks", []):
                        s = check.get("status", "info")
                        if s in counts:
                            counts[s] += 1
            # Cable test results
            for ct in device.get("cable_tests", []):
                s = ct.get("status", "info")
                if s in counts:
                    counts[s] += 1

    return counts
