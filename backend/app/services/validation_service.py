"""
Post-deployment validation service.

Validates a Mist site by checking template variables, AP health,
switch health (including VC and cable tests), and gateway health.
"""

import asyncio
import copy
import functools
import json
import logging
import re
from datetime import datetime, timezone

import mistapi
from mistapi import APISession
from mistapi.api.v1.orgs import (
    deviceprofiles as org_deviceprofiles,
)
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
from mistapi.api.v1.orgs import (
    templates as org_templates_api,
)
from mistapi.api.v1.orgs import (
    devices as org_devices,
)
from mistapi.api.v1.orgs import (
    ssr as org_ssr,
)
from mistapi.api.v1.orgs import (
    inventory as org_inventory,
)
from mistapi.api.v1.orgs import (
    setting as org_setting,
)
from mistapi.api.v1.orgs import (
    sites as org_sites,
)
from mistapi.api.v1.orgs import (
    stats as org_stats,
)
from mistapi.api.v1.orgs import (
    wlans as org_wlans,
)
from mistapi.api.v1.self import (
    usage as self_usage,
)
from mistapi.api.v1.sites import (
    devices,
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
from app.utils.event_definitions import EVENT_CATEGORY_DISPLAY, EVENT_TYPE_MAP, extract_sub_id
from app.utils.variables import extract_variables

logger = logging.getLogger(__name__)

# Step definitions: (id, default_label)
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
]


class _ProgressTracker:
    """Manages structured step-based progress and broadcasts via callback."""

    def __init__(self, report_id: str, progress_callback, steps: list[tuple[str, str]] | None = None) -> None:
        self.report_id = report_id
        self._callback = progress_callback
        step_defs = steps or _STEPS
        self.steps: dict[str, dict] = {
            sid: {"id": sid, "label": label, "status": "pending", "message": ""} for sid, label in step_defs
        }
        self.overall_completed = 0
        self.overall_total = 0  # 0 = discovery phase (indeterminate)

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

        Execution-phase steps: aps, switches, gateways, cable_tests (N ports), config_errors.
        If cable_test_ports == 0, cable_tests still counts as 1 step.
        """
        self.overall_total = 4 + max(cable_test_ports, 1)  # aps + switches + gateways + cable tests + config errors
        self.overall_completed = 0

    async def complete_cable_test_port(self, message: str) -> None:
        """Increment progress for one completed cable test port."""
        self.steps["cable_tests"]["message"] = message
        self.overall_completed += 1
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
    include_config_errors: bool = False,
    progress_callback=None,
    token: str | None = None,
    cookies: dict | None = None,
    csrftoken: str | None = None,
) -> None:
    """Run the full post-deployment validation for a site."""
    from app.services.mist_service import MistService
    from app import db

    tracker = _ProgressTracker(job_id, progress_callback)

    try:
        mist = MistService(org_id=org_id, cloud_region=cloud_region, api_token=token, cookies=cookies, csrftoken=csrftoken)
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
        site_setting_resp, org_setting_resp = await asyncio.gather(
            mistapi.arun(setting.getSiteSetting, session, site_id),
            mistapi.arun(org_setting.getOrgSettings, session, mist.org_id),
        )
        site_settings = site_setting_resp.data if site_setting_resp.status_code == 200 else {}
        site_vars = site_settings.get("vars") or {}
        site_auto_upgrade = site_settings.get("auto_upgrade") or {}
        org_settings = org_setting_resp.data if org_setting_resp.status_code == 200 else {}

        site_data = await mist.get_site(site_id)
        site_name = site_data.get("name", site_id)
        await db.update_job(job_id, site_name=site_name, status="running")

        site_address = site_data.get("address", "")
        sitegroup_ids = site_data.get("sitegroup_ids", [])
        sitegroup_names = await _resolve_sitegroup_names(session, mist.org_id, sitegroup_ids)
        await tracker.complete_step("site_info")

        # Step 2: Fetch assigned templates + WLANs + gateway template
        await tracker.start_step("templates", "Checking templates and WLANs...")
        assigned_templates, assigned_template_data, gw_template_config = await _fetch_assigned_templates(session, mist.org_id, site_data)
        derived_sources = await _fetch_derived_sources(session, site_id)
        wlan_info = await _fetch_wlan_info(session, site_id)

        # Determine which networks are actually assigned to gateway ports.
        # The derived template may drop range keys (e.g. "ge-0/0/2-3"), so
        # fall back to ip_configs keys only when port_config yields nothing.
        used_network_names = _used_networks_from_port_config(gw_template_config.get("port_config", {}))
        if not used_network_names:
            used_network_names = set(gw_template_config.get("ip_configs", {}).keys())

        # Filter derived sources to exclude unused networks and applications
        used_services = _extract_used_services(gw_template_config, derived_sources)
        _FILTERABLE_TEMPLATES = {"gateway_template", "network_template"}

        def _filter_derived(ttype: str, tlist: list) -> list:
            if ttype == "network":
                # Only scan networks actually assigned to gateway ports
                return [t for t in tlist if t.get("name") in used_network_names]
            if ttype == "application":
                # Only scan applications explicitly referenced in service policies
                return [t for t in tlist if t.get("name") in used_services]
            return tlist

        derived_sources = [(ttype, _filter_derived(ttype, tlist)) for ttype, tlist in derived_sources]
        assigned_template_data = [
            (ttype, [_filter_template_networks(t, used_network_names) for t in tlist] if ttype in _FILTERABLE_TEMPLATES else tlist)
            for ttype, tlist in assigned_template_data
        ]

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

        # Step 4+5: Fetch all device events (single API call), partition config vs raw
        await tracker.start_step("config_events", "Fetching device events...")
        config_events, raw_device_events = await _fetch_all_device_events(session, site_id)
        await tracker.complete_step("config_events")

        await tracker.start_step("device_events", "Correlating device events...")
        events_by_mac = _correlate_device_events(raw_device_events)
        total_correlated = sum(len(v) for v in events_by_mac.values())
        await tracker.complete_step("device_events", f"{total_correlated} events correlated")

        # ── Execution phase (determinate progress bar) ───────────────

        # Parallel fetch: all device stats + switch ports
        (up_ports_by_mac, sw_optics_by_mac), sw_stats_resp, ap_stats_resp, gw_stats_resp = await asyncio.gather(
            _fetch_switch_ports(session, site_id),
            mistapi.arun(stats.listSiteDevicesStats, session, site_id, type="switch", limit=1000),
            mistapi.arun(stats.listSiteDevicesStats, session, site_id, type="ap", limit=1000),
            mistapi.arun(stats.listSiteDevicesStats, session, site_id, type="gateway", limit=1000),
        )
        switch_stats = sw_stats_resp.data if sw_stats_resp.status_code == 200 else []
        ap_stats = ap_stats_resp.data if ap_stats_resp.status_code == 200 else []
        gw_stats = gw_stats_resp.data if gw_stats_resp.status_code == 200 else []

        # Collect unique models for firmware version lookup (full model strings, e.g. "EX4100-48MP")
        ap_models = {d.get("model", "") for d in ap_stats if d.get("model")}
        switch_models = {d.get("model", "") for d in switch_stats if d.get("model")}
        srx_models: set[str] = set()
        ssr_macs: list[tuple[str, str]] = []
        for gw in gw_stats:
            m, mac = gw.get("model", ""), gw.get("mac", "")
            if m.upper().startswith("SSR"):
                ssr_macs.append((m, mac))
            elif m:
                srx_models.add(m)

        # Fetch recommended firmware versions (3 + N_ssr API calls, all in parallel)
        fw_versions = await _fetch_firmware_versions(session, mist.org_id, ap_models, switch_models, srx_models, ssr_macs)

        # Override recommended firmware with auto_upgrade settings.
        # 3-tier: baseline → org override → site override (applied in sequence).
        _apply_ap_auto_upgrade(fw_versions, org_settings.get("auto_upgrade", {}))
        _apply_ap_auto_upgrade(fw_versions, site_auto_upgrade)
        _apply_junos_auto_upgrade(fw_versions, "switch", org_settings.get("switch", {}).get("auto_upgrade", {}))
        _apply_junos_auto_upgrade(fw_versions, "gateway", org_settings.get("juniper_srx", {}).get("auto_upgrade", {}))

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
        result["aps"] = _validate_aps(ap_stats, config_events, fw_versions)
        tracker.update_label("aps", f"Access Points ({len(result['aps'])})")
        await tracker.complete_step("aps", f"{len(result['aps'])} APs validated")

        # Step 6: Switch health validation (parallel, no cable tests)
        tracker.update_label("switches", f"Switches ({len(switch_stats)})")
        await tracker.start_step("switches", "Validating switches...")
        result["switches"] = _validate_switch_health(switch_stats, config_events, fw_versions)
        # Attach LLDP neighbors from UP ports (available regardless of cable tests)
        for sw_result in result["switches"]:
            sw_mac = sw_result["mac"]
            up_ports = up_ports_by_mac.get(sw_mac, [])
            lldp_neighbors = []
            for p in up_ports:
                sys_name = p.get("neighbor_system_name", "")
                port_desc = p.get("neighbor_port_desc", "")
                if sys_name or port_desc:
                    lldp_neighbors.append({
                        "port_id": p["port_id"],
                        "neighbor_system_name": sys_name,
                        "neighbor_port_desc": port_desc,
                    })
            sw_result["lldp_neighbors"] = lldp_neighbors
            # Attach optics data and aggregated check
            optics = sw_optics_by_mac.get(sw_mac, [])
            sw_result["port_optics"] = optics
            sw_result["checks"].append(_build_optics_check(optics))
        await tracker.complete_step("switches", f"{len(result['switches'])} switches validated")

        # Step 7: Gateway validation
        tracker.update_label("gateways", "Gateways")
        await tracker.start_step("gateways", "Validating gateways...")
        result["gateways"] = await _validate_gateways(session, mist.org_id, site_id, gw_stats, config_events, site_vars, gw_template_config, fw_versions)
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

        # Step 9: Config command errors (opt-in)
        if include_config_errors:
            sw_gw_devices = result["switches"] + result["gateways"]
            count = len(sw_gw_devices)
            tracker.update_label("config_errors", f"Config Command Errors ({count} devices)")
            await tracker.start_step("config_errors", f"Checking {count} devices...")
            await _run_all_config_error_checks(session, site_id, sw_gw_devices, tracker)
            await tracker.complete_step("config_errors", f"{count} devices checked")
        else:
            await tracker.start_step("config_errors", "Skipped (opt-in)")
            await tracker.complete_step("config_errors", "Skipped (opt-in)")

        # Attach correlated events to each device
        _attach_device_events(result, events_by_mac)

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
        user_msg = "Validation failed. Please check your credentials and try again."
        await db.update_job(job_id, status="failed", error=user_msg)

        if progress_callback:
            await progress_callback(
                job_id,
                {
                    "type": "report_complete",
                    "data": {"status": "failed", "report_id": job_id, "error": user_msg},
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


def _firmware_check(device: dict, fw_versions: dict | None = None, device_type: str = "ap") -> dict:
    """Build a firmware_version check dict with validation against recommended version.

    For APs: baseline tag = pass, deprecated/alpha = fail, other = warn.
    For switches/SRX: junos_suggested tag = pass, other = warn.
    For SSR: latest stable = pass, other = warn.
    """
    fw = device.get("version", device.get("fw_version", ""))
    if not fw:
        return {"check": "firmware_version", "status": "info", "value": "unknown"}

    model = device.get("model", "")
    mac = device.get("mac", "")

    version_info = None
    if fw_versions:
        if device_type == "ssr":
            version_info = fw_versions.get("ssr", {}).get(mac)
        else:
            version_info = fw_versions.get(device_type, {}).get(model)

    if not version_info or not version_info.get("recommended"):
        return {"check": "firmware_version", "status": "info", "value": fw}

    recommended = version_info["recommended"]
    if fw == recommended:
        status = "pass"
    elif device_type == "ap" and (fw in version_info.get("deprecated", set()) or fw in version_info.get("alpha", set())):
        status = "fail"
    else:
        status = "warn"

    return {"check": "firmware_version", "status": status, "value": fw, "expected": recommended}


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


async def _resolve_sitegroup_names(session: APISession, org_id: str, sitegroup_ids: list[str]) -> list[str]:
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


# ── Firmware version lookup ─────────────────────────────────────────────


def _parse_ap_versions(versions: list[dict]) -> dict[str, dict]:
    """Parse AP version list into per-model lookup with recommended/deprecated/alpha."""
    by_model: dict[str, list[dict]] = {}
    for v in versions:
        model = v.get("model", "")
        if model:
            by_model.setdefault(model, []).append(v)

    result: dict[str, dict] = {}
    for model, vlist in by_model.items():
        recommended = ""
        deprecated: set[str] = set()
        alpha: set[str] = set()
        for v in vlist:
            ver = v.get("version", "")
            tags = v.get("tags", []) or []
            if not ver:
                continue
            if "baseline" in tags:
                recommended = ver
            if "deprecated" in tags:
                deprecated.add(ver)
            if "alpha" in tags:
                alpha.add(ver)
        # If no baseline, pick highest version as recommended
        if not recommended:
            all_versions = [v.get("version", "") for v in vlist if v.get("version")]
            if all_versions:
                all_versions.sort()
                recommended = all_versions[-1]
        result[model] = {"recommended": recommended, "deprecated": deprecated, "alpha": alpha}
    return result


def _parse_junos_versions(versions: list[dict]) -> dict[str, dict]:
    """Parse switch/SRX version list into per-model lookup with junos_suggested."""
    by_model: dict[str, list[dict]] = {}
    for v in versions:
        model = v.get("model", "")
        if model:
            by_model.setdefault(model, []).append(v)

    result: dict[str, dict] = {}
    for model, vlist in by_model.items():
        recommended = ""
        for v in vlist:
            ver = v.get("version", "")
            tags = v.get("tags", []) or []
            if ver and "junos_suggested" in tags:
                recommended = ver
                break
        result[model] = {"recommended": recommended}
    return result


def _parse_ssr_versions(versions: list[dict]) -> str:
    """Pick the latest (first) stable SSR version as recommended."""
    if not versions:
        return ""
    # API returns versions in order; pick the first (latest)
    return versions[0].get("version", "") if isinstance(versions[0], dict) else ""


async def _fetch_firmware_versions(
    session: APISession,
    org_id: str,
    ap_models: set[str],
    switch_models: set[str],
    srx_gateway_models: set[str],
    ssr_macs: list[tuple[str, str]],
) -> dict:
    """Fetch recommended firmware versions from Mist API.

    Makes 3 + N_ssr API calls in parallel:
      - 1 call for APs (all models comma-separated)
      - 1 call for switches (all models comma-separated)
      - 1 call for SRX gateways (all models comma-separated)
      - N calls for SSR gateways (one per MAC)

    Returns a lookup dict:
    {
        "ap":      {model: {"recommended": ver, "deprecated": {ver,...}, "alpha": {ver,...}}},
        "switch":  {model: {"recommended": ver}},
        "gateway": {model: {"recommended": ver}},
        "ssr":     {mac: {"recommended": ver}},
    }
    """
    result: dict[str, dict] = {"ap": {}, "switch": {}, "gateway": {}, "ssr": {}}

    async def _fetch_device_versions(device_type: str, models: set[str]) -> tuple[str, list[dict]]:
        if not models:
            return device_type, []
        model_str = ",".join(sorted(models))
        try:
            resp = await mistapi.arun(
                org_devices.listOrgAvailableDeviceVersions,
                session, org_id, type=device_type, model=model_str,
            )
            if resp.status_code == 200 and isinstance(resp.data, list):
                logger.debug("firmware_versions_fetched type=%s models=%s count=%d", device_type, model_str, len(resp.data))
                return device_type, resp.data
        except Exception as e:
            logger.warning("firmware_versions_fetch_failed type=%s error=%s", device_type, str(e))
        return device_type, []

    async def _fetch_ssr_version(mac: str) -> tuple[str, str]:
        try:
            resp = await mistapi.arun(
                org_ssr.listOrgAvailableSsrVersions,
                session, org_id, channel="stable", mac=mac,
            )
            if resp.status_code == 200 and isinstance(resp.data, list):
                return mac, _parse_ssr_versions(resp.data)
        except Exception as e:
            logger.warning("ssr_version_fetch_failed mac=%s error=%s", mac, str(e))
        return mac, ""

    # Build all coroutines
    tasks: list = [
        _fetch_device_versions("ap", ap_models),
        _fetch_device_versions("switch", switch_models),
        _fetch_device_versions("gateway", srx_gateway_models),
    ]
    for _model, mac in ssr_macs:
        tasks.append(_fetch_ssr_version(mac))

    results = await asyncio.gather(*tasks)

    # Parse device version results (first 3)
    for dtype, versions in results[:3]:
        if not versions:
            continue
        if dtype == "ap":
            result["ap"] = _parse_ap_versions(versions)
        else:
            result[dtype] = _parse_junos_versions(versions)

    # Parse SSR results
    for mac, recommended in results[3:]:
        if recommended:
            result["ssr"][mac] = {"recommended": recommended}

    return result


def _apply_ap_auto_upgrade(fw_versions: dict, auto_upgrade: dict) -> None:
    """Override AP recommended firmware based on site auto_upgrade settings.

    Mutates fw_versions["ap"] in place.
    """
    if not auto_upgrade.get("enabled"):
        return

    mode = auto_upgrade.get("version", "stable")
    custom_versions = auto_upgrade.get("custom_versions", {})

    for model, info in fw_versions.get("ap", {}).items():
        if mode == "beta":
            # Pick the alpha-tagged version as recommended
            alpha_versions = sorted(info.get("alpha", set()))
            if alpha_versions:
                info["recommended"] = alpha_versions[-1]
            # Clear alpha set so running alpha isn't flagged as fail
            info["alpha"] = set()

        elif mode == "custom":
            custom_ver = custom_versions.get(model)
            if custom_ver:
                info["recommended"] = custom_ver


def _apply_junos_auto_upgrade(fw_versions: dict, device_type: str, auto_upgrade: dict) -> None:
    """Override switch/SRX recommended firmware based on org auto_upgrade settings.

    Supports two modes:
    - ``version`` field: a single version string applied to all models
    - ``custom_versions`` dict: per-model overrides (takes priority over ``version``)

    Mutates fw_versions[device_type] in place.
    """
    if not auto_upgrade.get("enabled"):
        return

    blanket_version = auto_upgrade.get("version", "")
    custom_versions = auto_upgrade.get("custom_versions", {})
    for model, info in fw_versions.get(device_type, {}).items():
        custom_ver = custom_versions.get(model)
        if custom_ver:
            info["recommended"] = custom_ver
        elif blanket_version:
            info["recommended"] = blanket_version


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
    session: APISession, org_id: str, site_data: dict
) -> tuple[list[dict], list[tuple[str, list[dict]]], dict]:
    """Fetch assigned templates by ID from site info.

    Fetches all assigned templates in parallel via ``asyncio.gather()``.

    Returns:
        (site_info_entries, template_data_for_var_scan, gw_template_config)
        - site_info_entries: list of {"type", "name", "id"} for site_info display
        - template_data_for_var_scan: list of (template_type, [full_template_dict]) for variable extraction
        - gw_template_config: the raw gateway template dict (or {} if not assigned)
    """
    # Build tasks for all assigned templates
    tasks = []
    for field, tmpl_type, api_fn in _ASSIGNED_TEMPLATE_FIELDS:
        tmpl_id = site_data.get(field)
        if tmpl_id:
            tasks.append(_fetch_one_assigned_template(session, org_id, field, tmpl_type, api_fn, tmpl_id))

    if not tasks:
        return [], [], {}

    results = await asyncio.gather(*tasks, return_exceptions=True)

    site_info_entries: list[dict] = []
    var_scan_data: list[tuple[str, list[dict]]] = []
    gw_template_config: dict = {}

    for result in results:
        if isinstance(result, Exception):
            logger.warning("assigned_template_fetch_error error=%s", str(result))
            continue
        _field, tmpl_type, tmpl_id, data = result
        if data:
            site_info_entries.append({"type": tmpl_type, "name": data.get("name", tmpl_id[:8]), "id": tmpl_id})
            var_scan_data.append((tmpl_type, [data]))
            if tmpl_type == "gateway_template":
                gw_template_config = data
        else:
            site_info_entries.append({"type": tmpl_type, "name": tmpl_id[:8], "id": tmpl_id})

    return site_info_entries, var_scan_data, gw_template_config


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


async def _fetch_derived_sources(session: APISession, site_id: str) -> list[tuple[str, list[dict]]]:
    """Fetch derived objects (networks, services, service policies) for variable scanning."""
    results = await asyncio.gather(
        *[_fetch_one_template(session, site_id, ttype, fn, kw) for ttype, fn, kw in _DERIVED_SOURCES]
    )
    return [r for r in results if r is not None]


async def _fetch_wlan_info(session: APISession, site_id: str) -> dict:
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
        # Strip smsMessageFormat — contains Mist built-in vars ({{code}}, {{duration}})
        wlan_to_scan = w
        portal = w.get("portal")
        if isinstance(portal, dict) and "smsMessageFormat" in portal:
            wlan_to_scan = {**w, "portal": {k: v for k, v in portal.items() if k != "smsMessageFormat"}}
        for var_name in sorted(_extract_jinja2_vars(wlan_to_scan)):
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


# ── Device events (single fetch, partitioned) ───────────────────────────

_CONFIG_EVENT_PREFIXES = ("AP_CONFIG", "SW_CONFIG", "GW_CONFIG")


async def _fetch_all_device_events(
    session: APISession, site_id: str,
) -> tuple[dict[str, dict], list[dict]]:
    """Fetch all device events for the site in the last 24h with a single API call.

    Returns:
        (config_events, raw_events) where:
        - config_events: dict mapping device MAC → latest config event summary
        - raw_events: full list of event dicts for trigger/clear correlation
    """
    config_events: dict[str, dict] = {}
    all_events: list[dict] = []

    try:
        resp = await mistapi.arun(
            devices.searchSiteDeviceEvents,
            session,
            site_id,
            limit=1000,
            duration="24h",
        )
        if resp.status_code != 200:
            logger.warning("device_events_fetch_failed status=%s", resp.status_code)
            return config_events, all_events

        data = resp.data
        if isinstance(data, dict):
            results = data.get("results")
            all_events = results if isinstance(results, list) else []
        elif isinstance(data, list):
            all_events = data

        logger.debug("device_events_fetched count=%d", len(all_events))

        # Partition: extract config events
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
            existing = config_events.get(mac)
            if existing and existing["timestamp"] >= timestamp:
                continue

            is_success = ev_type.endswith("_CONFIGURED") or ev_type.endswith("_CONFIG_CHANGED_BY_USER")
            config_events[mac] = {
                "type": ev_type,
                "timestamp": timestamp,
                "status": "pass" if is_success else "fail",
            }
    except Exception as e:
        logger.warning("device_events_fetch_error error=%s", str(e))

    return config_events, all_events


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


def _correlate_device_events(raw_events: list[dict]) -> dict[str, list[dict]]:
    """Correlate trigger/clear event pairs and group by device MAC.

    Returns dict mapping MAC → list of correlated event summaries.
    """
    # Sort chronologically so the last update wins
    try:
        sorted_events = sorted(raw_events, key=lambda e: e.get("timestamp", 0))
    except (TypeError, KeyError):
        sorted_events = raw_events

    # Track state per (mac, category, sub_id)
    tracking: dict[tuple[str, str, str], dict] = {}

    for event in sorted_events:
        if not isinstance(event, dict):
            continue
        ev_type = event.get("type", "")
        mapping = EVENT_TYPE_MAP.get(ev_type)
        if not mapping:
            continue

        category, role, sub_id_field = mapping
        mac = event.get("mac", event.get("device_mac", ""))
        if not mac:
            continue

        sub_id = extract_sub_id(event, sub_id_field)
        key = (mac, category, sub_id)

        entry = tracking.get(key)
        if not entry:
            entry = {
                "category": category,
                "display": EVENT_CATEGORY_DISPLAY.get(category, category),
                "sub_id": sub_id or None,
                "status": "",
                "trigger_count": 0,
                "clear_count": 0,
                "last_change": 0,
            }
            tracking[key] = entry

        timestamp = event.get("timestamp", 0)
        if role == "trigger":
            entry["status"] = "triggered"
            entry["trigger_count"] += 1
        else:
            entry["status"] = "cleared"
            entry["clear_count"] += 1
        entry["last_change"] = max(entry["last_change"], timestamp)

    # Group by MAC
    result: dict[str, list[dict]] = {}
    for (mac, _category, _sub_id), entry in tracking.items():
        result.setdefault(mac, []).append(entry)

    # Sort each device's events: triggered first, then by category
    for mac in result:
        result[mac].sort(key=lambda e: (0 if e["status"] == "triggered" else 1, e["category"]))

    return result


def _attach_device_events(report_result: dict, events_by_mac: dict[str, list[dict]]) -> None:
    """Attach correlated events to each device in the report result."""
    for device_type in ("aps", "switches", "gateways"):
        for device in report_result.get(device_type, []):
            mac = device.get("mac", "")
            device["events"] = events_by_mac.get(mac, [])


# ── AP validation ────────────────────────────────────────────────────────


def _validate_aps(ap_stats: list[dict], config_events: dict[str, dict], fw_versions: dict | None = None) -> list[dict]:
    """Validate all APs at the site."""
    results: list[dict] = []
    for ap in ap_stats:
        checks: list[dict] = []
        mac = ap.get("mac", "")

        # Name check
        name = ap.get("name", "")
        checks.append(_name_check(name))

        # Firmware version
        checks.append(_firmware_check(ap, fw_versions, device_type="ap"))

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


# ── Optics helpers ───────────────────────────────────────────────────────

_RX_POWER_WARN = -20.0  # dBm
_RX_POWER_FAIL = -25.0
_TX_POWER_WARN = -8.0
_TX_POWER_FAIL = -12.0


def _optics_power_status(value: float | None, warn_threshold: float, fail_threshold: float) -> str:
    """Return pass/warn/fail/info for an optics power reading."""
    if value is None:
        return "info"
    if value < fail_threshold:
        return "fail"
    if value < warn_threshold:
        return "warn"
    return "pass"


def _extract_port_optics(port: dict) -> dict | None:
    """Extract optics info from a port stats entry. Returns None if no transceiver."""
    xcvr_model = port.get("xcvr_model")
    if not xcvr_model:
        return None
    rx = port.get("optics_rx_power")
    tx = port.get("optics_tx_power")
    return {
        "port_id": port.get("port_id", ""),
        "media_type": port.get("media_type", ""),
        "xcvr_model": xcvr_model,
        "xcvr_serial": port.get("xcvr_serial", ""),
        "xcvr_part_number": port.get("xcvr_part_number", ""),
        "rx_power": rx,
        "tx_power": tx,
        "rx_power_status": _optics_power_status(rx, _RX_POWER_WARN, _RX_POWER_FAIL),
        "tx_power_status": _optics_power_status(tx, _TX_POWER_WARN, _TX_POWER_FAIL),
        "temperature": port.get("optics_module_temperature"),
        "bias_current": port.get("optics_bias_current"),
        "voltage": port.get("optics_module_voltage"),
    }


def _build_optics_check(optics: list[dict]) -> dict:
    """Build aggregated optics_health check from per-port optics list."""
    if not optics:
        return {"check": "optics_health", "status": "info", "value": "No optics"}
    fail_count = sum(1 for o in optics if o["rx_power_status"] == "fail" or o["tx_power_status"] == "fail")
    warn_count = sum(
        1 for o in optics
        if (o["rx_power_status"] == "warn" or o["tx_power_status"] == "warn")
        and o["rx_power_status"] != "fail" and o["tx_power_status"] != "fail"
    )
    label = f"{len(optics)} optics"
    if fail_count:
        label += f" ({fail_count} fail)"
        overall = "fail"
    elif warn_count:
        label += f" ({warn_count} warn)"
        overall = "warn"
    else:
        has_reading = any(o["rx_power_status"] == "pass" or o["tx_power_status"] == "pass" for o in optics)
        overall = "pass" if has_reading else "info"
    return {"check": "optics_health", "status": overall, "value": label}


# ── Switch validation ────────────────────────────────────────────────────


# Physical copper interface prefixes eligible for cable tests
_COPPER_PORT_PREFIXES = ("ge-", "mge-", "nge-")


def _is_copper_port(port_id: str) -> bool:
    """Check if a port ID is a physical copper Ethernet interface."""
    return port_id.startswith(_COPPER_PORT_PREFIXES)


async def _fetch_switch_ports(
    session: APISession, site_id: str,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Fetch all switch ports in a single API call.

    Returns a tuple of:
      - copper_up_ports_by_mac: MAC → list of UP copper port dicts (for cable tests / LLDP)
      - optics_by_mac: MAC → list of optics entries (ports with a transceiver)
    """
    copper_up: dict[str, list[dict]] = {}
    optics: dict[str, list[dict]] = {}
    try:
        resp = await mistapi.arun(
            stats.searchSiteSwOrGwPorts,
            session,
            site_id,
            device_type="switch",
            limit=1000,
        )
        if resp.status_code != 200:
            logger.warning("switch_ports_fetch_failed status=%s", resp.status_code)
            return copper_up, optics

        results = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        if not isinstance(results, list):
            return copper_up, optics

        for port in results:
            if not isinstance(port, dict):
                continue
            device_mac = port.get("mac", "")
            port_id = port.get("port_id", "")
            if not device_mac or not port_id:
                continue

            # Copper UP ports (for cable tests and LLDP neighbors)
            if port.get("up") and _is_copper_port(port_id):
                copper_up.setdefault(device_mac, []).append(
                    {
                        "port_id": port_id,
                        "neighbor_system_name": port.get("neighbor_system_name", ""),
                        "neighbor_port_desc": port.get("neighbor_port_desc", ""),
                    }
                )

            # Optics data (any port with a transceiver)
            optic = _extract_port_optics(port)
            if optic:
                optics.setdefault(device_mac, []).append(optic)

        # Sort per-device optics lists by port_id
        for mac in optics:
            optics[mac].sort(key=lambda o: o["port_id"])

    except Exception as e:
        logger.warning("switch_ports_fetch_error error=%s", str(e))

    return copper_up, optics


def _validate_switch_health(switch_stats: list[dict], config_events: dict[str, dict], fw_versions: dict | None = None) -> list[dict]:
    """Validate all switches (health only, no cable tests). Synchronous — no API calls needed."""
    return [_validate_single_switch(sw, config_events, fw_versions) for sw in switch_stats]


def _validate_single_switch(sw: dict, config_events: dict[str, dict], fw_versions: dict | None = None) -> dict:
    """Validate a single switch: name, firmware, status, VC. No cable tests."""
    checks: list[dict] = []
    device_id = sw.get("id", "")
    name = sw.get("name", "")
    mac = sw.get("mac", "")

    checks.append(_name_check(name))
    firmware = sw.get("version", sw.get("fw_version", ""))
    checks.append(_firmware_check(sw, fw_versions, device_type="switch"))

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
        "config_errors": [],
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
                "member_id": mod.get("vc_member", mod.get("fpc_idx", idx)),
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


async def _run_cable_test(session: APISession, site_id: str, device_id: str, port_id: str) -> dict:
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


# ── Config command error checks ──────────────────────────────────────────


async def _run_all_config_error_checks(
    session: APISession,
    site_id: str,
    device_results: list[dict],
    tracker: "_ProgressTracker",
) -> None:
    """Fetch config_cmd for each switch/gateway and attach any _errors to the device result."""
    sem = asyncio.Semaphore(10)

    async def _check_one_device(dev_result: dict) -> None:
        device_id = dev_result.get("device_id", "")
        name = dev_result.get("name", device_id)
        if not device_id:
            return
        async with sem:
            try:
                resp = await mistapi.arun(devices.getSiteDeviceConfigCmd, session, site_id, device_id)
                if resp.status_code == 200 and isinstance(resp.data, dict):
                    errors = resp.data.get("_errors", [])
                    if not isinstance(errors, list):
                        errors = []
                else:
                    errors = None  # Unable to retrieve
            except Exception as e:
                logger.warning("config_cmd_failed device_id=%s error=%s", device_id, str(e))
                errors = None

        if errors is None:
            dev_result["checks"].append({"check": "config_errors", "status": "info", "value": "Unable to retrieve"})
        else:
            dev_result["config_errors"] = errors
            dev_result["checks"].append({
                "check": "config_errors",
                "status": "pass" if not errors else "warn",
                "value": "No errors" if not errors else f"{len(errors)} error(s)",
            })

        await tracker.update_step("config_errors", f"Checked {name}")

    await asyncio.gather(*[_check_one_device(d) for d in device_results])


# ── Gateway validation ───────────────────────────────────────────────────



async def _fetch_device_profiles(session: APISession, org_id: str, device_configs: list[dict]) -> dict[str, dict]:
    """Fetch device profiles referenced by device configs, cached by unique ID."""
    profile_ids: set[str] = set()
    for cfg in device_configs:
        dp_id = cfg.get("deviceprofile_id")
        if dp_id:
            profile_ids.add(dp_id)

    if not profile_ids:
        return {}

    profile_map: dict[str, dict] = {}

    async def _fetch_one(dp_id: str) -> None:
        try:
            resp = await mistapi.arun(org_deviceprofiles.getOrgDeviceProfile, session, org_id, dp_id)
            if resp.status_code == 200 and isinstance(resp.data, dict):
                profile_map[dp_id] = resp.data
        except Exception as e:
            logger.warning("deviceprofile_fetch_failed dp_id=%s error=%s", dp_id, str(e))

    await asyncio.gather(*[_fetch_one(dp_id) for dp_id in profile_ids])
    return profile_map


def _used_networks_from_port_config(port_config: dict) -> set[str]:
    """Extract the set of network names actually assigned to ports."""
    names: set[str] = set()
    for cfg in port_config.values():
        if not isinstance(cfg, dict):
            continue
        usage = cfg.get("usage", "")
        if usage and usage not in ("wan", "lan"):
            names.add(usage)
        for net in cfg.get("networks", []):
            if isinstance(net, str):
                names.add(net)
        pn = cfg.get("port_network", "")
        if pn:
            names.add(pn)
    return names


def _extract_used_services(gw_template_config: dict, derived_sources: list) -> set[str]:
    """Collect service names explicitly referenced in service policies.

    "any" is a wildcard and is excluded — when only "any" is used, no specific
    application definitions need variable scanning.
    """
    names: set[str] = set()
    for sp in gw_template_config.get("service_policies", []):
        if isinstance(sp, dict):
            for svc in sp.get("services", []):
                if isinstance(svc, str) and svc != "any":
                    names.add(svc)
    for ttype, tlist in derived_sources:
        if ttype == "application_policy":
            for sp in tlist:
                if isinstance(sp, dict):
                    for svc in sp.get("services", []):
                        if isinstance(svc, str) and svc != "any":
                            names.add(svc)
    return names


def _filter_template_networks(tmpl: dict, used_networks: set[str]) -> dict:
    """Return a shallow copy of a gateway template with only used network configs.

    This prevents variable scanning from flagging variables in networks that
    aren't assigned to any port at this site.
    """
    filtered = dict(tmpl)
    for key in ("ip_configs", "dhcpd_config"):
        if key in filtered and isinstance(filtered[key], dict):
            filtered[key] = {k: v for k, v in filtered[key].items() if k in used_networks}
    if "networks" in filtered and isinstance(filtered["networks"], list):
        filtered["networks"] = [
            n for n in filtered["networks"]
            if isinstance(n, dict) and n.get("name") in used_networks
        ]
    return filtered


async def _validate_gateways(
    session: APISession, org_id: str, site_id: str, gw_stats: list[dict],
    config_events: dict[str, dict], site_vars: dict,
    gw_template_config: dict | None = None, fw_versions: dict | None = None,
) -> list[dict]:
    """Validate all gateways at the site with port classification and network details."""
    if not gw_stats:
        return []

    # Use pre-fetched template or default to empty
    if gw_template_config is None:
        gw_template_config = {}

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

    # Fetch device profiles for gateways that have one assigned
    device_profile_map = await _fetch_device_profiles(session, org_id, list(device_config_map.values()))

    results = await asyncio.gather(
        *[
            _validate_single_gateway(
                gw_stat,
                gw_template_config,
                device_profile_map.get(
                    device_config_map.get(gw_stat.get("id", ""), {}).get("deviceprofile_id", ""), {}
                ),
                device_config_map.get(gw_stat.get("id", ""), {}),
                port_stats_by_mac.get(gw_stat.get("mac", ""), {}),
                config_events,
                site_vars,
                fw_versions,
            )
            for gw_stat in gw_stats
        ]
    )
    return list(results)


_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


def _parse_interface_key(key: str) -> list[str]:
    """Expand an interface key that may contain ranges or commas.

    Supported formats (can be combined):
      - ``ge-0/0/2-7``  → ge-0/0/2 … ge-0/0/7
      - ``ge-0/0/2,ge-0/0/6`` → ge-0/0/2, ge-0/0/6
      - ``ge-0/0/2-4,ge-0/0/6`` → ge-0/0/2, ge-0/0/3, ge-0/0/4, ge-0/0/6

    Non-standard names without ``/`` (e.g. ``ae2``) pass through unchanged.
    """
    result: list[str] = []
    for segment in key.split(","):
        segment = segment.strip()
        if not segment:
            continue
        slash_idx = segment.rfind("/")
        if slash_idx == -1:
            result.append(segment)
            continue
        prefix = segment[: slash_idx + 1]
        port_spec = segment[slash_idx + 1 :]
        m = _RANGE_RE.match(port_spec)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if start <= end:
                result.extend(f"{prefix}{n}" for n in range(start, end + 1))
            else:
                result.append(segment)
        else:
            result.append(segment)
    return result


def _expand_port_config_ranges(port_config: dict) -> dict:
    """Expand range/comma keys in a port_config dict into individual interfaces.

    Each expanded interface receives an independent deep copy of the config so
    that downstream mutations on one port don't affect others.
    """
    expanded: dict[str, dict] = {}
    for key, cfg in port_config.items():
        interfaces = _parse_interface_key(key)
        for iface in interfaces:
            expanded[iface] = copy.deepcopy(cfg) if isinstance(cfg, dict) else cfg
    return expanded


def _merge_port_configs(*configs: dict) -> dict:
    """Merge port_config from multiple config layers (template → profile → device).

    Range keys (e.g. ``ge-0/0/2-7``) are expanded into individual interfaces
    before merging so that a device-level override for a single port correctly
    inherits template-level fields like ``aggregated`` and ``ae_idx``.
    """
    all_keys: set[str] = set()
    expanded_configs: list[dict] = []
    for cfg in configs:
        expanded_pc = _expand_port_config_ranges(cfg.get("port_config", {}))
        expanded_configs.append(expanded_pc)
        all_keys.update(expanded_pc.keys())

    merged: dict[str, dict] = {}
    for key in all_keys:
        port_merged: dict = {}
        for epc in expanded_configs:
            pc = epc.get(key, {})
            if isinstance(pc, dict):
                port_merged.update(pc)
        merged[key] = port_merged
    return merged


async def _validate_single_gateway(
    gw_stat: dict,
    gw_template_config: dict,
    deviceprofile_config: dict,
    device_config: dict,
    port_stats_map: dict[str, dict],
    config_events: dict[str, dict],
    site_vars: dict,
    fw_versions: dict | None = None,
) -> dict:
    """Validate a single gateway: name, firmware, status, ports, networks."""
    device_id = gw_stat.get("id", "")
    name = gw_stat.get("name", "")
    mac = gw_stat.get("mac", "")

    # Merge: template → deviceprofile → device
    # port_config uses per-port deep merge to preserve template fields (aggregated, ae_idx)
    port_config = _merge_port_configs(gw_template_config, deviceprofile_config, device_config)
    ip_configs = {**gw_template_config.get("ip_configs", {}), **deviceprofile_config.get("ip_configs", {}), **device_config.get("ip_configs", {})}
    dhcpd_config = {**gw_template_config.get("dhcpd_config", {}), **deviceprofile_config.get("dhcpd_config", {}), **device_config.get("dhcpd_config", {})}
    networks_list = device_config.get("networks") or deviceprofile_config.get("networks") or gw_template_config.get("networks", [])

    # Resolve Jinja2 variables in the final merged configs
    port_config = _resolve_all_vars(port_config, site_vars)
    ip_configs = _resolve_all_vars(ip_configs, site_vars)
    dhcpd_config = _resolve_all_vars(dhcpd_config, site_vars)
    networks_list = _resolve_all_vars(networks_list, site_vars)

    # Basic checks
    checks: list[dict] = []
    checks.append(_name_check(name))
    gw_fw_type = "ssr" if gw_stat.get("model", "").upper().startswith("SSR") else "gateway"
    checks.append(_firmware_check(gw_stat, fw_versions, device_type=gw_fw_type))

    gw_status = gw_stat.get("status", "unknown")
    checks.append({"check": "connection_status", "status": _device_conn_status(gw_status), "value": gw_status})

    _add_config_status_check(checks, mac, config_events)

    # Detect ae interfaces from port stats and supplement port_config.
    # The derived gateway template may expand/drop range keys like "ge-0/0/2-3",
    # losing aggregated/ae_idx metadata.  Port stats always has ae interfaces.
    ae_member_ports: set[str] = set()  # member ports to skip in iteration
    for port_id, ps in port_stats_map.items():
        if not port_id.startswith("ae") or not isinstance(ps, dict):
            continue
        if port_id in port_config:
            continue  # already covered
        # Build config for ae from its member ports or if_stat
        ae_cfg: dict = {"usage": ps.get("port_usage", "lan"), "aggregated": True}
        # Collect network info from if_stat subinterfaces
        if_stat_data = gw_stat.get("if_stat", {})
        ae_networks: list[str] = []
        ae_port_network = ""
        for if_key, if_data in if_stat_data.items():
            if isinstance(if_data, dict) and if_data.get("port_id") == port_id:
                net = if_data.get("network_name", "")
                vlan = if_data.get("vlan", 0)
                if net:
                    if vlan == 0:
                        ae_port_network = net
                    elif net not in ae_networks:
                        ae_networks.append(net)
        if ae_port_network:
            ae_cfg["port_network"] = ae_port_network
        if ae_networks:
            ae_cfg["networks"] = ae_networks
        port_config[port_id] = ae_cfg
        # Track member ports so we skip them in the iteration
        for member_id, mstat in port_stats_map.items():
            if isinstance(mstat, dict) and mstat.get("port_parent") == port_id:
                ae_member_ports.add(member_id)

    # Pre-resolve ae indices for aggregated ports (from port_config metadata)
    ae_idx_map: dict[str, str] = {}
    for iface, cfg in port_config.items():
        if isinstance(cfg, dict) and cfg.get("aggregated"):
            idx = cfg.get("ae_idx")
            if idx is not None and str(idx) != "":
                ae_idx_map[iface] = f"ae{idx}"
        # ae ports detected from stats are already named correctly (e.g., "ae2")
        if iface.startswith("ae"):
            ae_idx_map[iface] = iface

    # Classify ports using port_config
    wan_ports: list[dict] = []
    lan_ports: list[dict] = []
    if_stat = gw_stat.get("if_stat", {})

    for iface, cfg in port_config.items():
        if not isinstance(cfg, dict):
            continue
        # Skip ae member ports — they're covered by the ae entry
        if iface in ae_member_ports:
            continue
        usage = cfg.get("usage", "")
        # Skip unconfigured ports: no usage, or port stats says unconfigured
        if not usage:
            continue
        iface_stats = port_stats_map.get(iface, {})
        if iface_stats.get("unconfigured"):
            continue

        # Resolve effective interface for LACP aggregated ports
        effective_iface = ae_idx_map.get(iface, iface)

        logger.debug(
            "gw_port_resolve name=%s iface=%s effective=%s aggregated=%s usage=%s",
            name, iface, effective_iface, cfg.get("aggregated"), usage,
        )

        # Look up port stats; fall back to if_stat for ae/subinterface ports
        pstat = port_stats_map.get(effective_iface, {})
        if not pstat:
            # Check if_stat: port is up if any subinterface with this port_id is up
            port_up = False
            found = False
            for if_key, if_data in if_stat.items():
                if isinstance(if_data, dict) and if_data.get("port_id") == effective_iface:
                    found = True
                    if if_data.get("up"):
                        port_up = True
                        logger.debug("gw_port_ifstat_match name=%s effective=%s if_key=%s up=True", name, effective_iface, if_key)
                        break
            if found:
                pstat = {"up": port_up}

        is_up = pstat.get("up", False)
        neighbor_sys = pstat.get("neighbor_system_name", "")
        neighbor_port = pstat.get("neighbor_port_desc", "")

        # Collect LACP member details for ae interfaces
        members: list[dict] = []
        if iface in ae_idx_map:
            # From lacp_stats on the ae port stats entry
            for lacp in pstat.get("lacp_stats", []):
                member_name = lacp.get("name", "")
                member_stat = port_stats_map.get(member_name, {})
                members.append({
                    "interface": member_name,
                    "up": member_stat.get("up", False),
                    "neighbor_system_name": member_stat.get("neighbor_system_name", ""),
                    "neighbor_port_desc": member_stat.get("neighbor_port_desc", ""),
                })
            # If no lacp_stats, try finding members via port_parent
            if not members:
                for pid, ps in port_stats_map.items():
                    if isinstance(ps, dict) and ps.get("port_parent") == effective_iface:
                        members.append({
                            "interface": pid,
                            "up": ps.get("up", False),
                            "neighbor_system_name": ps.get("neighbor_system_name", ""),
                            "neighbor_port_desc": ps.get("neighbor_port_desc", ""),
                        })

        if usage == "wan":
            entry: dict = {
                "interface": effective_iface,
                "name": cfg.get("name", ""),
                "up": is_up,
                "wan_type": cfg.get("wan_type", cfg.get("wan_source", "")),
                "neighbor_system_name": neighbor_sys,
                "neighbor_port_desc": neighbor_port,
            }
            if members:
                entry["members"] = members
            wan_ports.append(entry)
        else:
            # For trunk ports (usage="lan"), list the actual networks
            networks = cfg.get("networks", [])
            port_network = cfg.get("port_network", "")
            if usage == "lan":
                network_label = ", ".join([port_network] + networks) if port_network else ", ".join(networks)
            else:
                network_label = usage

            entry = {
                "interface": effective_iface,
                "network": network_label,
                "up": is_up,
                "neighbor_system_name": neighbor_sys,
                "neighbor_port_desc": neighbor_port,
            }
            if members:
                entry["members"] = members
            lan_ports.append(entry)

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

    # Networks with IP and DHCP details (filtered to port-assigned networks)
    gw_networks: list[dict] = _build_network_details(ip_configs, dhcpd_config, networks_list, port_config)

    # Optics data from port stats
    port_optics: list[dict] = []
    for port_id, pstat in port_stats_map.items():
        if not isinstance(pstat, dict):
            continue
        optic = _extract_port_optics(pstat)
        if optic:
            port_optics.append(optic)
    port_optics.sort(key=lambda o: o["port_id"])
    checks.append(_build_optics_check(port_optics))

    # Cluster / HA detection
    cluster_result = None
    if gw_stat.get("is_ha"):
        cluster_result = _check_gateway_cluster(gw_stat)

    return {
        "device_id": device_id,
        "name": name or "(unnamed)",
        "mac": mac,
        "model": gw_stat.get("model", ""),
        "checks": checks,
        "cluster": cluster_result,
        "wan_ports": wan_ports,
        "lan_ports": lan_ports,
        "networks": gw_networks,
        "port_optics": port_optics,
        "config_errors": [],
    }


def _check_gateway_cluster(gw_stat: dict) -> dict:
    """Check gateway HA cluster nodes using module_stat / module2_stat."""
    expected_firmware = gw_stat.get("version", gw_stat.get("fw_version", ""))
    nodes: list[tuple[str, dict]] = []

    # Collect nodes from module_stat (node0) and module2_stat (node1)
    for idx, stat_key in enumerate(("module_stat", "module2_stat")):
        mod_list = gw_stat.get(stat_key, [])
        if isinstance(mod_list, list):
            for mod in mod_list:
                if not isinstance(mod, dict):
                    continue
                nodes.append((f"node{idx}", mod))

    members: list[dict] = []
    for default_name, mod in nodes:
        node_fw = mod.get("version", "")
        node_status = mod.get("status", "")
        ha_state = mod.get("ha_state", "")

        fw_match = bool(node_fw and node_fw == expected_firmware)
        member_checks: list[dict] = [
            {
                "check": "firmware_match",
                "status": "pass" if fw_match else ("fail" if node_fw else "info"),
                "value": node_fw or "unknown",
                "expected": expected_firmware,
            },
            {
                "check": "node_connected",
                "status": "pass" if node_status == "connected" else "fail",
                "value": node_status or "unknown",
            },
        ]

        members.append({
            "node_name": mod.get("node_name") or mod.get("router_name") or default_name,
            "mac": mod.get("mac", ""),
            "serial": mod.get("serial", ""),
            "model": mod.get("model", ""),
            "firmware": node_fw,
            "status": node_status,
            "ha_state": ha_state,
            "checks": member_checks,
        })

    # Cluster-level status from cluster_stat (SSR) or cluster_config (SRX)
    cluster_state = gw_stat.get("cluster_stat", {}).get("state", "")
    cluster_config = gw_stat.get("cluster_config")
    config_summary = None
    if isinstance(cluster_config, dict):
        cluster_state = cluster_state or cluster_config.get("status", "")
        config_summary = {
            "configuration": cluster_config.get("configuration", ""),
            "operational": cluster_config.get("operational", ""),
            "primary_node_health": cluster_config.get("primary_node_health", ""),
            "secondary_node_health": cluster_config.get("secondary_node_health", ""),
            "control_link": cluster_config.get("control_link_info", {}),
            "fabric_link": cluster_config.get("fabric_link_info", {}),
            "reth_interfaces": cluster_config.get("ethernet_connection", []),
        }

    result: dict = {"status": cluster_state or "checked", "members": members}
    if config_summary:
        result["config"] = config_summary
    return result


@functools.lru_cache(maxsize=1)
def _get_jinja_env():
    """Get or create shared Jinja2 SandboxedEnvironment (thread-safe singleton)."""
    from app.utils.variables import create_jinja_env

    return create_jinja_env()


def _resolve_vars(text: str, site_vars: dict) -> str:
    """Resolve Jinja2 variables in a string using site vars."""
    if not text or "{{" not in text:
        return text
    try:
        return _get_jinja_env().from_string(text).render(site_vars)
    except Exception:
        return text


def _resolve_all_vars(data: object, site_vars: dict) -> object:
    """Recursively resolve Jinja2 variables in all string values of a data structure."""
    if isinstance(data, str):
        return _resolve_vars(data, site_vars)
    if isinstance(data, dict):
        return {k: _resolve_all_vars(v, site_vars) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_all_vars(item, site_vars) for item in data]
    return data


def _build_network_details(ip_configs: dict, dhcpd_config: dict, networks_list: list, port_config: dict | None = None) -> list[dict]:
    """Build network details (IP, DHCP) for a gateway.

    All Jinja2 variables must be resolved before calling this function.

    When *port_config* is provided, only networks that are actually referenced
    as a ``usage`` value in a port are included.  This avoids listing org-level
    networks that aren't assigned to any gateway port at this site.
    """
    # Collect candidate network names from ip_configs keys and networks list
    network_names: set[str] = set(ip_configs.keys())
    for net in networks_list:
        if isinstance(net, dict) and net.get("name"):
            network_names.add(net["name"])

    # Filter to only networks actually assigned to a port
    if port_config:
        network_names = network_names & _used_networks_from_port_config(port_config)

    results: list[dict] = []
    for net_name in sorted(network_names):
        ip_cfg = ip_configs.get(net_name, {})
        if not isinstance(ip_cfg, dict):
            ip_cfg = {}
        dhcp_cfg = dhcpd_config.get(net_name, {})
        if not isinstance(dhcp_cfg, dict):
            dhcp_cfg = {}

        # Gateway IP (already resolved upstream via _resolve_all_vars)
        gateway_ip = ip_cfg.get("ip", "")
        netmask = ip_cfg.get("netmask", ip_cfg.get("prefix_length", ""))
        if gateway_ip and netmask:
            netmask_str = str(netmask).lstrip("/")
            if "." in netmask_str:
                cidr = sum(bin(int(x)).count("1") for x in netmask_str.split("."))
                gateway_ip = f"{gateway_ip}/{cidr}"
            else:
                gateway_ip = f"{gateway_ip}/{netmask_str}"

        # DHCP status (already resolved upstream via _resolve_all_vars)
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


# ══════════════════════════════════════════════════════════════════════════
# Org-level validation
# ══════════════════════════════════════════════════════════════════════════

_ORG_STEPS = [
    ("preflight", "Pre-flight Check"),
    ("org_data", "Organization Data"),
    ("device_stats", "Device Statistics"),
    ("port_stats", "Port Statistics"),
    ("firmware", "Firmware Versions"),
    ("device_events", "Device Events"),
    ("variables", "Template Variables"),
    ("ap_validation", "AP Validation"),
    ("sw_validation", "Switch Validation"),
    ("gw_validation", "Gateway Validation"),
    ("config_errors", "Config Command Errors"),
]


async def _paginate_org(api_func, session: APISession, org_id: str, limit: int = 1000, **kwargs) -> list:
    """Fetch all pages from a paginated org-level API endpoint."""
    all_results: list = []
    page = 1
    resp = await mistapi.arun(api_func, session, org_id, limit=limit, **kwargs)
    if resp.status_code == 200:
        all_results = mistapi.get_all(session, resp)
    else:
        logger.warning("org_paginate_failed func=%s page=%d status=%d", api_func.__name__, page, resp.status_code)
    return all_results


async def check_org_api_budget(
    session: APISession, org_id: str, include_config_errors: bool = False,
) -> dict:
    """Pre-flight check: estimate API call budget for an org-level report."""
    try:
        usage_resp, inv_resp, sites_resp = await asyncio.gather(
            mistapi.arun(self_usage.getSelfApiUsage, session),
            mistapi.arun(org_inventory.countOrgInventory, session, org_id, distinct="type"),
            mistapi.arun(org_sites.countOrgSites, session, org_id, distinct="id"),
        )
    except Exception as e:
        logger.warning("budget_check_failed error=%s", str(e))
        return {
            "allowed": False, "reason": f"Failed to check API budget: {e}",
            "available": 0, "estimated": 0,
            "config_errors_allowed": False, "config_errors_reason": "Budget check failed",
            "site_count": 0, "device_counts": {},
        }

    # Parse API usage
    requests_used = 0
    request_limit = 5000
    if usage_resp.status_code == 200 and isinstance(usage_resp.data, dict):
        requests_used = usage_resp.data.get("requests", 0)
        request_limit = usage_resp.data.get("request_limit", 5000)
    available = request_limit - requests_used

    # Parse device counts
    device_counts = {"ap": 0, "switch": 0, "gateway": 0}
    total_devices = 0
    if inv_resp.status_code == 200 and isinstance(inv_resp.data, dict):
        for item in inv_resp.data.get("results", []):
            if isinstance(item, dict):
                dtype = item.get("type", "")
                count = item.get("count", 0)
                if dtype in device_counts:
                    device_counts[dtype] = count
                    total_devices += count

    # Parse site count
    site_count = 0
    if sites_resp.status_code == 200 and isinstance(sites_resp.data, dict):
        site_count = sites_resp.data.get("total", 0)

    from math import ceil
    base_calls = 20
    pagination = ceil(total_devices / 1000) * 5
    site_settings_calls = site_count                      # getSiteSetting × every site
    assigned_template_calls = min(site_count * 4, 100)   # RF/network/gateway/site templates, mostly shared
    per_site_gw = int(site_count * 0.5) * 2  # rough estimate: 50% of sites have gateways
    config_error_calls = (device_counts["switch"] + device_counts["gateway"]) if include_config_errors else 0

    estimated_base = base_calls + pagination + site_settings_calls + assigned_template_calls + per_site_gw
    estimated_total = estimated_base + config_error_calls
    required = int(estimated_total * 1.15)

    allowed = required <= available

    # Check if config errors alone would bust the budget
    config_errors_allowed = True
    config_errors_reason = ""
    if include_config_errors:
        required_with_ce = int((estimated_base + config_error_calls) * 1.15)
        required_without_ce = int(estimated_base * 1.15)
        if required_with_ce > available and required_without_ce <= available:
            config_errors_allowed = False
            config_errors_reason = (
                f"Config errors need ~{config_error_calls} extra API calls. "
                f"Only {available} calls available (need {required_with_ce})."
            )
        elif required_with_ce > available:
            config_errors_allowed = False
            config_errors_reason = "Insufficient API budget for any org report."
    else:
        config_errors_reason = "Not requested"

    reason = "" if allowed else (
        f"Estimated {required} API calls but only {available} available "
        f"({requests_used}/{request_limit} used). "
        f"Org has {site_count} sites and {total_devices} devices. "
        f"Breakdown: {base_calls} base + {pagination} pagination + {site_settings_calls} site settings "
        f"+ {assigned_template_calls} templates + {per_site_gw} gateway config "
        f"+ {config_error_calls} config errors."
    )

    return {
        "allowed": allowed,
        "reason": reason,
        "available": available,
        "estimated": required,
        "config_errors_allowed": config_errors_allowed,
        "config_errors_reason": config_errors_reason,
        "site_count": site_count,
        "device_counts": device_counts,
    }


async def _fetch_org_device_events(
    session: APISession, org_id: str,
) -> tuple[dict[str, dict], list[dict]]:
    """Fetch all device events across the org in the last 24h.

    Returns (config_events, raw_events) — same shape as _fetch_all_device_events
    but keyed by MAC across all sites.
    """
    config_events: dict[str, dict] = {}
    all_events: list[dict] = []
    try:
        results = await _paginate_org(
            org_devices.searchOrgDeviceEvents, session, org_id,
            limit=1000, device_type="all", duration="24h",
        )
        all_events = results

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
            existing = config_events.get(mac)
            if existing and existing["timestamp"] >= timestamp:
                continue
            is_success = ev_type.endswith("_CONFIGURED") or ev_type.endswith("_CONFIG_CHANGED_BY_USER")
            config_events[mac] = {
                "type": ev_type,
                "timestamp": timestamp,
                "status": "pass" if is_success else "fail",
            }
    except Exception as e:
        logger.warning("org_device_events_fetch_error error=%s", str(e))

    return config_events, all_events


def _group_by_site(items: list[dict], site_key: str = "site_id") -> dict[str, list[dict]]:
    """Partition a flat list of dicts into per-site buckets."""
    grouped: dict[str, list[dict]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = item.get(site_key, "")
        if sid:
            grouped.setdefault(sid, []).append(item)
    return grouped


def _parse_org_port_stats(port_results: list[dict]) -> tuple[dict[str, dict[str, list[dict]]], dict[str, dict[str, list[dict]]]]:
    """Parse org-level port stats into per-site, per-mac structures.

    Returns (sw_ports_by_site, gw_ports_by_site) where each is:
      {site_id: {mac: [port_dict, ...]}}
    """
    sw_copper_up: dict[str, dict[str, list[dict]]] = {}
    sw_optics: dict[str, dict[str, list[dict]]] = {}
    gw_ports: dict[str, dict[str, dict[str, dict]]] = {}

    for port in port_results:
        if not isinstance(port, dict):
            continue
        site_id = port.get("site_id", "")
        device_mac = port.get("mac", "")
        port_id = port.get("port_id", "")
        device_type = port.get("device_type", "")
        if not site_id or not device_mac or not port_id:
            continue

        if device_type == "switch":
            if port.get("up") and _is_copper_port(port_id):
                sw_copper_up.setdefault(site_id, {}).setdefault(device_mac, []).append({
                    "port_id": port_id,
                    "neighbor_system_name": port.get("neighbor_system_name", ""),
                    "neighbor_port_desc": port.get("neighbor_port_desc", ""),
                })
            optic = _extract_port_optics(port)
            if optic:
                sw_optics.setdefault(site_id, {}).setdefault(device_mac, []).append(optic)
        elif device_type == "gateway":
            gw_ports.setdefault(site_id, {}).setdefault(device_mac, {})[port_id] = port

    return (sw_copper_up, sw_optics), gw_ports


def _template_applies_to_site(
    template: dict, site_id: str, site_sitegroup_ids: list[str],
) -> bool:
    """Check whether a template/WLAN applies to a given site based on its applies/exceptions rules."""
    applies = template.get("applies", {})
    exceptions = template.get("exceptions", {})

    # Check exceptions first
    if site_id in exceptions.get("site_ids", []):
        return False
    if any(sg in exceptions.get("sitegroup_ids", []) for sg in site_sitegroup_ids):
        return False

    # If org_id is in applies, it targets the whole org (minus exceptions)
    if "org_id" in applies:
        return True

    # If specific site_ids match
    if site_id in applies.get("site_ids", []):
        return True

    # If specific sitegroup_ids match any of the site's groups
    if any(sg in applies.get("sitegroup_ids", []) for sg in site_sitegroup_ids):
        return True

    # Empty applies (no site_ids, no sitegroup_ids, no org_id) means not assigned
    return False


async def run_org_validation(
    job_id: str,
    cloud_region: str,
    org_id: str,
    include_config_errors: bool = False,
    progress_callback=None,
    token: str | None = None,
    cookies: dict | None = None,
    csrftoken: str | None = None,
) -> None:
    """Run org-level validation across all sites."""
    from app.services.mist_service import MistService
    from app import db

    tracker = _ProgressTracker(job_id, progress_callback, steps=_ORG_STEPS)

    try:
        mist = MistService(
            org_id=org_id,
            cloud_region=cloud_region,
            api_token=token,
            cookies=cookies,
            csrftoken=csrftoken,
        )
        session = mist.get_session()
        org_name = ""

        # ── Step 1: Pre-flight ──
        await tracker.start_step("preflight", "Checking API budget...")
        budget = await check_org_api_budget(session, org_id, include_config_errors)
        if not budget["allowed"]:
            raise RuntimeError(f"Insufficient API budget: {budget['reason']}")
        # Adjust config errors if budget doesn't allow
        if include_config_errors and not budget["config_errors_allowed"]:
            include_config_errors = False
            logger.info("org_validation config_errors disabled: %s", budget["config_errors_reason"])
        await tracker.complete_step("preflight", "API budget OK", increment=False)
        await db.update_job(job_id, status="running")

        # ── Step 2: Org data ──
        await tracker.start_step("org_data", "Fetching organization data...")
        org_info_resp = await mistapi.arun(session.mist_get, f"/api/v1/orgs/{org_id}")
        if org_info_resp.status_code == 200 and isinstance(org_info_resp.data, dict):
            org_name = org_info_resp.data.get("name", "")
        await db.update_job(job_id, org_name=org_name, site_name=org_name)

        # Parallel org-level fetches
        sites_task = _paginate_org(org_sites.listOrgSites, session, org_id, limit=1000)
        groups_task = mistapi.arun(org_sitegroups.listOrgSiteGroups, session, org_id, limit=1000)
        templates_task = mistapi.arun(org_templates_api.listOrgTemplates, session, org_id, limit=1000)
        org_wlans_task = mistapi.arun(org_wlans.listOrgWlans, session, org_id, limit=1000)
        org_settings_task = mistapi.arun(org_setting.getOrgSettings, session, org_id)

        sites_list, groups_resp, templates_resp, wlans_resp, osettings_resp = await asyncio.gather(
            sites_task, groups_task, templates_task, org_wlans_task, org_settings_task,
        )

        # Parse org data
        site_groups = groups_resp.data if groups_resp.status_code == 200 and isinstance(groups_resp.data, list) else []
        org_templates = templates_resp.data if templates_resp.status_code == 200 and isinstance(templates_resp.data, list) else []
        org_wlans_list = wlans_resp.data if wlans_resp.status_code == 200 and isinstance(wlans_resp.data, list) else []
        org_settings = osettings_resp.data if osettings_resp.status_code == 200 and isinstance(osettings_resp.data, dict) else {}

        # Build site lookup from listOrgSites
        site_map: dict[str, dict] = {}
        for s in sites_list:
            if isinstance(s, dict) and s.get("id"):
                site_map[s["id"]] = s

        # Fetch site settings for all sites in parallel (gives us vars, auto_upgrade, template overrides)
        site_settings_map: dict[str, dict] = {}
        settings_sem = asyncio.Semaphore(10)

        async def _fetch_site_settings(site_id: str) -> None:
            async with settings_sem:
                try:
                    resp = await mistapi.arun(setting.getSiteSetting, session, site_id)
                    if resp.status_code == 200 and isinstance(resp.data, dict):
                        site_settings_map[site_id] = resp.data
                except Exception as e:
                    logger.warning("site_settings_fetch_failed site_id=%s error=%s", site_id, str(e))

        await asyncio.gather(*[_fetch_site_settings(sid) for sid in site_map])
        await tracker.update_step("org_data", f"{len(site_map)} sites, {len(site_settings_map)} settings fetched")

        # Build sitegroup lookup
        group_map: dict[str, str] = {}
        for g in site_groups:
            if isinstance(g, dict) and g.get("id"):
                group_map[g["id"]] = g.get("name", "")

        await tracker.complete_step("org_data", f"{len(site_map)} sites, {len(org_templates)} templates", increment=False)

        # ── Step 3: Device stats ──
        await tracker.start_step("device_stats", "Fetching device statistics...")
        ap_stats_task = _paginate_org(org_stats.listOrgDevicesStats, session, org_id, limit=1000, type="ap")
        sw_stats_task = _paginate_org(org_stats.listOrgDevicesStats, session, org_id, limit=1000, type="switch")
        gw_stats_task = _paginate_org(org_stats.listOrgDevicesStats, session, org_id, limit=1000, type="gateway")

        all_ap_stats, all_sw_stats, all_gw_stats = await asyncio.gather(
            ap_stats_task, sw_stats_task, gw_stats_task,
        )
        total_devices = len(all_ap_stats) + len(all_sw_stats) + len(all_gw_stats)
        await tracker.complete_step("device_stats", f"{total_devices} devices", increment=False)

        # ── Step 4: Port stats ──
        await tracker.start_step("port_stats", "Fetching port statistics...")
        all_port_stats = await _paginate_org(org_stats.searchOrgSwOrGwPorts, session, org_id, limit=1000)
        (sw_copper_up_by_site, sw_optics_by_site), gw_ports_by_site = _parse_org_port_stats(all_port_stats)
        await tracker.complete_step("port_stats", f"{len(all_port_stats)} ports", increment=False)

        # ── Step 5: Firmware versions ──
        await tracker.start_step("firmware", "Fetching firmware versions...")
        ap_models = {d.get("model", "") for d in all_ap_stats if d.get("model")}
        switch_models = {d.get("model", "") for d in all_sw_stats if d.get("model")}
        srx_models: set[str] = set()
        ssr_macs: list[tuple[str, str]] = []
        for gw in all_gw_stats:
            m, mac = gw.get("model", ""), gw.get("mac", "")
            if m.upper().startswith("SSR"):
                ssr_macs.append((m, mac))
            elif m:
                srx_models.add(m)
        fw_versions = await _fetch_firmware_versions(session, org_id, ap_models, switch_models, srx_models, ssr_macs)
        _apply_ap_auto_upgrade(fw_versions, org_settings.get("auto_upgrade", {}))
        _apply_junos_auto_upgrade(fw_versions, "switch", org_settings.get("switch", {}).get("auto_upgrade", {}))
        _apply_junos_auto_upgrade(fw_versions, "gateway", org_settings.get("juniper_srx", {}).get("auto_upgrade", {}))
        await tracker.complete_step("firmware", "Firmware versions resolved", increment=False)

        # ── Step 6: Device events ──
        await tracker.start_step("device_events", "Fetching device events...")
        config_events, raw_events = await _fetch_org_device_events(session, org_id)
        events_by_mac = _correlate_device_events(raw_events)
        await tracker.complete_step("device_events", f"{len(raw_events)} events", increment=False)

        # ── Step 7: Variables ──
        await tracker.start_step("variables", "Checking template variables...")

        # Build template_id -> template lookup for WLAN scoping (site templates from listOrgTemplates)
        template_by_id: dict[str, dict] = {}
        for tmpl in org_templates:
            if isinstance(tmpl, dict) and tmpl.get("id"):
                template_by_id[tmpl["id"]] = tmpl

        # Fetch assigned templates (RF, network, gateway, site) — unique IDs only
        # Template IDs are in the site object (listOrgSites), NOT in getSiteSetting
        assigned_tmpl_cache: dict[str, tuple[str, dict]] = {}  # tmpl_id -> (type, content)
        unique_assigned: list[tuple[str, str, object, str]] = []  # (field, type, api_fn, id)
        for sid in site_map:
            sd = site_map[sid]
            for field, tmpl_type, api_fn in _ASSIGNED_TEMPLATE_FIELDS:
                tmpl_id = sd.get(field)
                if tmpl_id and tmpl_id not in assigned_tmpl_cache:
                    assigned_tmpl_cache[tmpl_id] = (tmpl_type, {})  # placeholder
                    unique_assigned.append((field, tmpl_type, api_fn, tmpl_id))

        if unique_assigned:
            fetch_tasks = [
                _fetch_one_assigned_template(session, org_id, f, t, fn, tid)
                for f, t, fn, tid in unique_assigned
            ]
            fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            for result in fetch_results:
                if isinstance(result, Exception):
                    continue
                _field, tmpl_type, tmpl_id, data = result
                if data:
                    assigned_tmpl_cache[tmpl_id] = (tmpl_type, data)

        await tracker.complete_step("variables", f"{len(assigned_tmpl_cache)} assigned templates fetched", increment=False)

        # ── Group data by site ──
        ap_by_site = _group_by_site(all_ap_stats)
        sw_by_site = _group_by_site(all_sw_stats)
        gw_by_site = _group_by_site(all_gw_stats)

        # Determine which sites have devices
        all_site_ids = set(site_map.keys())
        sites_with_gateways = {sid for sid in all_site_ids if sid in gw_by_site}

        # Switch to determinate progress: 3 device-type steps + config_errors
        tracker.overall_total = 3 + 1  # ap + sw + gw + config_errors
        tracker.overall_completed = 0

        # ── Step 8: AP validation ──
        await tracker.start_step("ap_validation", f"Validating {len(all_ap_stats)} APs...")
        site_results: dict[str, dict] = {}
        site_fw_map: dict[str, dict] = {}  # per-site fw_versions with site AP auto_upgrade
        for site_id in all_site_ids:
            site_data = site_map.get(site_id, {})
            site_settings = site_settings_map.get(site_id, {})
            site_ap_stats = ap_by_site.get(site_id, [])
            site_vars = site_settings.get("vars") or {}

            # Per-site firmware: apply site AP auto_upgrade on top of org (3-tier override)
            site_auto_upgrade = site_settings.get("auto_upgrade") or {}
            site_fw = {**fw_versions, "ap": copy.deepcopy(fw_versions.get("ap", {}))}
            _apply_ap_auto_upgrade(site_fw, site_auto_upgrade)
            site_fw_map[site_id] = site_fw

            # Build site info
            sg_ids = site_data.get("sitegroup_ids", [])
            sg_names = [group_map.get(gid, "") for gid in sg_ids if group_map.get(gid)]

            # Filter templates that apply to this site
            site_templates: list[dict] = []
            for tmpl in org_templates:
                if isinstance(tmpl, dict) and _template_applies_to_site(tmpl, site_id, sg_ids):
                    site_templates.append(tmpl)

            # Filter org WLANs that apply to this site (via their linked template)
            site_org_wlans: list[dict] = []
            for w in org_wlans_list:
                if not isinstance(w, dict):
                    continue
                wlan_tmpl_id = w.get("template_id", "")
                if wlan_tmpl_id and wlan_tmpl_id in template_by_id:
                    wlan_scope = template_by_id[wlan_tmpl_id]
                else:
                    wlan_scope = {"applies": {"org_id": org_id}}
                if _template_applies_to_site(wlan_scope, site_id, sg_ids):
                    site_org_wlans.append(w)

            # Build templates_raw for _validate_template_variables — same format as single-site report
            site_templates_raw: list[tuple[str, list[dict]]] = []
            for t in site_templates:
                site_templates_raw.append(("site_template", [t]))
            for field, tmpl_type, _ in _ASSIGNED_TEMPLATE_FIELDS:
                tmpl_id = site_data.get(field)
                if tmpl_id and tmpl_id in assigned_tmpl_cache:
                    _, at_content = assigned_tmpl_cache[tmpl_id]
                    if at_content:
                        site_templates_raw.append((tmpl_type, [at_content]))

            # Build templates list for site_info display
            site_template_info = [{"type": "site_template", "name": t.get("name", "")} for t in site_templates]
            for field, _at_type, _at_fn in _ASSIGNED_TEMPLATE_FIELDS:
                at_id = site_data.get(field)
                if at_id and at_id in assigned_tmpl_cache:
                    at_type, at_content = assigned_tmpl_cache[at_id]
                    if at_content:
                        site_template_info.append({"type": at_type, "name": at_content.get("name", at_id[:8])})

            site_result: dict = {
                "site_info": {
                    "site_name": site_data.get("name", ""),
                    "site_address": site_data.get("address", ""),
                    "site_groups": sg_names,
                    "templates": site_template_info,
                    "org_wlans": [{"ssid": w.get("ssid", "")} for w in site_org_wlans],
                    "site_wlans": [],
                    "device_summary": {},
                },
                "template_variables": _validate_template_variables(site_templates_raw, site_org_wlans, site_vars),
                "aps": _validate_aps(site_ap_stats, config_events, site_fw) if site_ap_stats else [],
                "switches": [],
                "gateways": [],
                "summary": {},
            }
            site_results[site_id] = site_result

        tracker.update_label("ap_validation", f"Access Points ({len(all_ap_stats)})")
        await tracker.complete_step("ap_validation", f"{len(all_ap_stats)} APs validated")

        # ── Step 9: Switch validation ──
        await tracker.start_step("sw_validation", f"Validating {len(all_sw_stats)} switches...")
        for site_id in all_site_ids:
            site_sw_stats = sw_by_site.get(site_id, [])
            if not site_sw_stats:
                continue
            sw_results = _validate_switch_health(site_sw_stats, config_events, fw_versions)
            # Attach LLDP neighbors and optics
            site_copper_up = sw_copper_up_by_site.get(site_id, {})
            site_sw_optics = sw_optics_by_site.get(site_id, {})
            for sw_result in sw_results:
                sw_mac = sw_result["mac"]
                up_ports = site_copper_up.get(sw_mac, [])
                lldp_neighbors = []
                for p in up_ports:
                    sys_name = p.get("neighbor_system_name", "")
                    port_desc = p.get("neighbor_port_desc", "")
                    if sys_name or port_desc:
                        lldp_neighbors.append({
                            "port_id": p["port_id"],
                            "neighbor_system_name": sys_name,
                            "neighbor_port_desc": port_desc,
                        })
                sw_result["lldp_neighbors"] = lldp_neighbors
                optics = site_sw_optics.get(sw_mac, [])
                sw_result["port_optics"] = optics
                sw_result["checks"].append(_build_optics_check(optics))
            site_results[site_id]["switches"] = sw_results

        tracker.update_label("sw_validation", f"Switches ({len(all_sw_stats)})")
        await tracker.complete_step("sw_validation", f"{len(all_sw_stats)} switches validated")

        # ── Step 10: Gateway validation ──
        await tracker.start_step("gw_validation", f"Validating {len(all_gw_stats)} gateways...")

        # Per-site gateway config fetches (parallel with semaphore)
        sem = asyncio.Semaphore(10)

        async def _validate_site_gateways(site_id: str) -> None:
            site_gw_stats = gw_by_site.get(site_id, [])
            if not site_gw_stats:
                return
            # Look up gateway template from assigned_tmpl_cache
            sd = site_map.get(site_id, {})
            gw_tmpl_id = sd.get("gatewaytemplate_id")
            site_gw_config = assigned_tmpl_cache.get(gw_tmpl_id, (None, {}))[1] if gw_tmpl_id else {}
            async with sem:
                try:
                    gw_results = await _validate_gateways(
                        session, org_id, site_id, site_gw_stats,
                        config_events, site_settings_map.get(site_id, {}).get("vars") or {},
                        gw_template_config=site_gw_config or None,
                        fw_versions=site_fw_map.get(site_id, fw_versions),
                    )
                    site_results[site_id]["gateways"] = gw_results
                except Exception as e:
                    logger.warning("org_gw_validation_failed site_id=%s error=%s", site_id, str(e))

        await asyncio.gather(*[_validate_site_gateways(sid) for sid in sites_with_gateways])
        tracker.update_label("gw_validation", f"Gateways ({len(all_gw_stats)})")
        await tracker.complete_step("gw_validation", f"{len(all_gw_stats)} gateways validated")

        # ── Step 11: Config errors (opt-in) ──
        if include_config_errors:
            all_sw_gw_devices = []
            for site_id, sr in site_results.items():
                for dev in sr.get("switches", []) + sr.get("gateways", []):
                    if dev.get("device_id"):
                        dev["_site_id"] = site_id
                        all_sw_gw_devices.append(dev)
            count = len(all_sw_gw_devices)
            tracker.update_label("config_errors", f"Config Command Errors ({count} devices)")
            await tracker.start_step("config_errors", f"Checking {count} devices...")

            # Run config error checks — need site_id per device
            ce_sem = asyncio.Semaphore(10)

            async def _check_one_ce(dev_result: dict) -> None:
                device_id = dev_result.get("device_id", "")
                dev_site_id = dev_result.pop("_site_id", "")
                name = dev_result.get("name", device_id)
                if not device_id or not dev_site_id:
                    return
                async with ce_sem:
                    try:
                        resp = await mistapi.arun(devices.getSiteDeviceConfigCmd, session, dev_site_id, device_id)
                        if resp.status_code == 200 and isinstance(resp.data, dict):
                            errors = resp.data.get("_errors", [])
                            if not isinstance(errors, list):
                                errors = []
                        else:
                            errors = None
                    except Exception as e:
                        logger.warning("config_cmd_failed device_id=%s error=%s", device_id, str(e))
                        errors = None
                if errors is None:
                    dev_result["checks"].append({"check": "config_errors", "status": "info", "value": "Unable to retrieve"})
                else:
                    dev_result["config_errors"] = errors
                    dev_result["checks"].append({
                        "check": "config_errors",
                        "status": "pass" if not errors else "warn",
                        "value": "No errors" if not errors else f"{len(errors)} error(s)",
                    })
                await tracker.update_step("config_errors", f"Checked {name}")

            await asyncio.gather(*[_check_one_ce(d) for d in all_sw_gw_devices])
            await tracker.complete_step("config_errors", f"{count} devices checked")
        else:
            await tracker.start_step("config_errors", "Skipped (opt-in)")
            await tracker.complete_step("config_errors", "Skipped (opt-in)")

        # ── Attach events and compute summaries ──
        for site_id, sr in site_results.items():
            _attach_device_events(sr, events_by_mac)
            sr["site_info"]["device_summary"] = _compute_device_summary(sr)
            sr["summary"] = _compute_summary(sr)

        # Build org-level summary
        org_summary = {"pass": 0, "fail": 0, "warn": 0, "info": 0}
        for sr in site_results.values():
            for key in org_summary:
                org_summary[key] += sr["summary"].get(key, 0)

        result = {
            "org_info": {
                "org_name": org_name,
                "org_id": org_id,
                "site_count": len(site_map),
                "device_counts": {
                    "aps": len(all_ap_stats),
                    "switches": len(all_sw_stats),
                    "gateways": len(all_gw_stats),
                },
            },
            "sites": site_results,
            "summary": org_summary,
        }

        await db.update_job(
            job_id,
            result=result,
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        if progress_callback:
            await progress_callback(job_id, {"type": "report_complete", "data": {"status": "completed", "report_id": job_id}})

        logger.info("org_validation_completed job_id=%s org_id=%s sites=%d devices=%d", job_id, org_id, len(site_results), total_devices)

    except Exception as e:
        logger.error("org_validation_failed job_id=%s error=%s", job_id, str(e), exc_info=True)
        user_msg = f"Org validation failed: {e}" if "Insufficient API budget" in str(e) else "Validation failed. Please check your credentials and try again."
        await db.update_job(job_id, status="failed", error=user_msg)

        if progress_callback:
            await progress_callback(
                job_id,
                {"type": "report_complete", "data": {"status": "failed", "report_id": job_id, "error": user_msg}},
            )
