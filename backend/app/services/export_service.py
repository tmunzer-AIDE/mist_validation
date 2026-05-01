"""
Export service for generating PDF and CSV reports from validation results.
"""

import csv
import datetime as _dt
import io
import types
import zipfile
from xml.sax.saxutils import escape as _html_escape

import logging
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logger = logging.getLogger(__name__)


def _checks_map(device: dict) -> dict:
    """Convert a device's checks list to a lookup dict keyed by check ID."""
    return {c["check"]: c for c in device.get("checks", [])}


def _checks_values(device: dict) -> dict:
    """Convert a device's checks list to a dict mapping check ID to value string."""
    return {c["check"]: c.get("value", "") for c in device.get("checks", [])}

# ── Colors & Styles ──────────────────────────────────────────────────────

_GREEN = colors.HexColor("#4caf50")
_RED = colors.HexColor("#f44336")
_ORANGE = colors.HexColor("#ff9800")
_BLUE = colors.HexColor("#2196f3")
_DARK = colors.HexColor("#37474f")
_LIGHT_GREEN = colors.HexColor("#e8f5e9")
_LIGHT_RED = colors.HexColor("#ffebee")
_LIGHT_ORANGE = colors.HexColor("#fff3e0")
_LIGHT_BLUE = colors.HexColor("#e3f2fd")
_ZEBRA_1 = colors.white
_ZEBRA_2 = colors.HexColor("#f5f5f5")

_PAGE_WIDTH = A4[0] - 3 * cm

_CELL_STYLE = ParagraphStyle("Cell", fontSize=7, leading=9)
_HEADER_STYLE = ParagraphStyle("Header", fontSize=7, leading=9, fontName="Helvetica-Bold", textColor=colors.white)
_CELL_GREEN = ParagraphStyle("CellGreen", fontSize=7, leading=9, textColor=_GREEN)
_CELL_RED = ParagraphStyle("CellRed", fontSize=7, leading=9, textColor=_RED)
_CELL_ORANGE = ParagraphStyle("CellOrange", fontSize=7, leading=9, textColor=_ORANGE)
_CELL_BLUE = ParagraphStyle("CellBlue", fontSize=7, leading=9, textColor=_BLUE)

_BASE_TABLE_STYLE = [
    ("BACKGROUND", (0, 0), (-1, 0), _DARK),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 7),
    ("TOPPADDING", (0, 1), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
    ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_ZEBRA_1, _ZEBRA_2]),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
]


def _esc(val) -> str:
    return _html_escape(str(val)) if val else ""


def _p(text, style=None) -> Paragraph:
    """Wrap text in a Paragraph for table cells."""
    return Paragraph(str(text) if text else "", style or _CELL_STYLE)


def _ph(text) -> Paragraph:
    """Header cell."""
    return Paragraph(str(text), _HEADER_STYLE)


def _p_status(status: str) -> Paragraph:
    """Colored status cell: Success/Failed/Warning/Info."""
    if status == "pass":
        return Paragraph("<b>Success</b>", _CELL_GREEN)
    if status == "fail":
        return Paragraph("<b>Failed</b>", _CELL_RED)
    if status == "warn":
        return Paragraph("<b>Warning</b>", _CELL_ORANGE)
    return Paragraph(str(status), _CELL_BLUE)


def _p_firmware(check: dict) -> Paragraph:
    """Render firmware cell with status coloring and recommended version."""
    fw = check.get("value", "")
    status = check.get("status", "info")
    expected = check.get("expected", "")
    style = {"pass": _CELL_GREEN, "fail": _CELL_RED, "warn": _CELL_ORANGE}.get(status, _CELL_STYLE)
    text = _esc(fw)
    if expected and status != "pass":
        text = f"{_esc(fw)}<br/><font size='5'>rec: {_esc(expected)}</font>"
    return Paragraph(text, style)


def _p_updown(is_up: bool) -> Paragraph:
    """Colored UP/DOWN cell."""
    if is_up:
        return Paragraph("<b>UP</b>", _CELL_GREEN)
    return Paragraph("<b>DOWN</b>", _CELL_RED)


def _p_optics_power(value, status: str) -> Paragraph:
    """Colored optics power value cell."""
    if value is None:
        return _p("—")
    text = f"<b>{value}</b>"
    if status == "pass":
        return Paragraph(text, _CELL_GREEN)
    if status == "warn":
        return Paragraph(text, _CELL_ORANGE)
    if status == "fail":
        return Paragraph(text, _CELL_RED)
    return _p(str(value))


def _lldp_str(item: dict) -> str:
    sys_name = item.get("neighbor_system_name", "")
    port = item.get("neighbor_port_desc", "")
    if sys_name and port:
        return f"{sys_name} ({port})"
    return sys_name or port or ""


def _make_table(data: list[list], col_widths: list[float] | None = None) -> Table:
    """Create a styled Table. All cells should already be Paragraphs."""
    if not data:
        return Table([[""]])
    if col_widths is None:
        col_count = len(data[0])
        col_widths = [_PAGE_WIDTH / col_count] * col_count
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(_BASE_TABLE_STYLE))
    return table


def _add_config_errors(elements: list, device: dict, normal_style) -> None:
    """Add config command errors list to PDF elements if any exist."""
    errors = device.get("config_errors", [])
    if not errors:
        return
    elements.append(Paragraph(f"<b>Configuration Errors ({len(errors)})</b>", normal_style))
    for err in errors:
        elements.append(Paragraph(f"\u2022 <font color='#ff9800'>{_esc(err)}</font>", _CELL_STYLE))


def _add_optics_table(elements: list, port_optics: list[dict], w: float) -> None:
    """Add an optics table + legend to the PDF elements list."""
    cw_op = [w * 0.12, w * 0.2, w * 0.14, w * 0.14, w * 0.14, w * 0.14, w * 0.12]
    data = [[_ph("Port"), _ph("Model"), _ph("Serial"), _ph("Part Number"), _ph("Rx (dBm)"), _ph("Tx (dBm)"), _ph("Temp (\u00b0C)")]]
    for o in port_optics:
        data.append([
            _p(o.get("port_id", "")),
            _p(o.get("xcvr_model", "")),
            _p(o.get("xcvr_serial", "")),
            _p(o.get("xcvr_part_number", "")),
            _p_optics_power(o.get("rx_power"), o.get("rx_power_status", "")),
            _p_optics_power(o.get("tx_power"), o.get("tx_power_status", "")),
            _p(str(o["temperature"]) if o.get("temperature") is not None else ""),
        ])
    elements.append(_make_table(data, cw_op))
    elements.append(Paragraph(
        "Rx threshold: warn &lt; -20 dBm, fail &lt; -25 dBm &bull; "
        "Tx threshold: warn &lt; -8 dBm, fail &lt; -12 dBm",
        ParagraphStyle("OpticsLegend", fontSize=6, leading=8, textColor=colors.grey),
    ))


def _optics_csv_rows(device_list: list[dict], name_key: str, mac_key: str) -> list[dict]:
    """Build optics CSV rows for a list of devices (switches or gateways)."""
    rows: list[dict] = []
    for dev in device_list:
        for o in dev.get("port_optics", []):
            rows.append({
                name_key: dev.get("name", ""), mac_key: dev.get("mac", ""),
                "port_id": o.get("port_id", ""), "media_type": o.get("media_type", ""),
                "xcvr_model": o.get("xcvr_model", ""), "xcvr_serial": o.get("xcvr_serial", ""),
                "xcvr_part_number": o.get("xcvr_part_number", ""),
                "rx_power": o.get("rx_power", ""), "rx_power_status": o.get("rx_power_status", ""),
                "tx_power": o.get("tx_power", ""), "tx_power_status": o.get("tx_power_status", ""),
                "temperature": o.get("temperature", ""),
                "bias_current": o.get("bias_current", ""),
                "voltage": o.get("voltage", ""),
            })
    return rows


def _adapt_report(report):
    """Adapt a dict report to a SimpleNamespace for attribute access."""
    if isinstance(report, dict):
        result_data = report.get("result") or {}
        r = types.SimpleNamespace(
            site_name=report.get("site_name", ""),
            result=result_data,
            completed_at=report.get("completed_at"),
            created_at=report.get("created_at"),
        )
        return r
    return report


# ── PDF ──────────────────────────────────────────────────────────────────


def generate_pdf(report) -> bytes:
    """Generate a PDF report from a completed ReportJob."""
    report = _adapt_report(report)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=18, spaceAfter=12)
    heading_style = ParagraphStyle("SectionHeading", parent=styles["Heading2"], fontSize=14, spaceAfter=8, spaceBefore=16)
    normal_style = styles["Normal"]

    elements: list = []
    result = report.result or {}
    site_info = result.get("site_info", {})

    # ── Title ──
    elements.append(Paragraph("Post Validation Report", title_style))
    elements.append(Paragraph(f"Site: {_esc(report.site_name)}", normal_style))
    gen_time = report.completed_at or report.created_at
    # Handle ISO string datetimes from SQLite
    if isinstance(gen_time, str):
        try:
            gen_time = _dt.datetime.fromisoformat(gen_time.replace("Z", "+00:00"))
        except Exception:
            gen_time = None
    gen_str = gen_time.strftime("%b %d, %Y, %H:%M") if gen_time and hasattr(gen_time, "strftime") else str(gen_time or "")
    elements.append(Paragraph(f"Generated: {gen_str} (UTC)", normal_style))
    elements.append(Spacer(1, 0.5 * cm))

    # ── Site Information ──
    if site_info:
        elements.append(Paragraph("Site Information", heading_style))
        site_address = site_info.get("site_address", "")
        site_groups = site_info.get("site_groups", [])
        if site_address:
            elements.append(Paragraph(f"<b>Address:</b> {_esc(site_address)}", normal_style))
        if site_groups:
            elements.append(Paragraph(f"<b>Site Groups:</b> {_esc(', '.join(site_groups))}", normal_style))

        tmpl_list = site_info.get("templates", [])
        if tmpl_list:
            elements.append(Spacer(1, 0.2 * cm))
            w = _PAGE_WIDTH
            data = [[_ph("Template Type"), _ph("Template Name")]]
            for t in tmpl_list:
                data.append([_p(t.get("type", "")), _p(t.get("name", ""))])
            elements.append(_make_table(data, [w * 0.4, w * 0.6]))

        org_wlans = site_info.get("org_wlans", [])
        site_wlans = site_info.get("site_wlans", [])
        if org_wlans or site_wlans:
            elements.append(Spacer(1, 0.2 * cm))
            data = [[_ph("SSID"), _ph("Source")]]
            for wl in org_wlans:
                data.append([_p(wl.get("ssid", "")), _p("Org (template)")])
            for wl in site_wlans:
                data.append([_p(wl.get("ssid", "")), _p("Site")])
            elements.append(_make_table(data, [w * 0.6, w * 0.4]))
        elements.append(Spacer(1, 0.3 * cm))

    # ── Device Summary ──
    ds = site_info.get("device_summary", {})
    if ds:
        elements.append(Paragraph("Device Summary", heading_style))
        w = _PAGE_WIDTH
        cw = [w * 0.22, w * 0.26, w * 0.26, w * 0.26]

        def _ds_vals(device_type: str) -> tuple[int, int, int]:
            d = ds.get(device_type, {})
            total = d.get("total", 0)
            failed = d.get("failed", 0)
            return total, total - failed, failed

        ap_t, ap_s, ap_f = _ds_vals("aps")
        sw_t, sw_s, sw_f = _ds_vals("switches")
        gw_t, gw_s, gw_f = _ds_vals("gateways")

        data = [
            [_ph(""), _ph("Access Points"), _ph("Switches"), _ph("Gateways")],
            [_p("Total"), _p(str(ap_t)), _p(str(sw_t)), _p(str(gw_t))],
            [_p("Success"), Paragraph(f"<b>{ap_s}</b>", _CELL_GREEN), Paragraph(f"<b>{sw_s}</b>", _CELL_GREEN), Paragraph(f"<b>{gw_s}</b>", _CELL_GREEN)],
            [_p("Failed"), Paragraph(f"<b>{ap_f}</b>", _CELL_RED if ap_f else _CELL_STYLE), Paragraph(f"<b>{sw_f}</b>", _CELL_RED if sw_f else _CELL_STYLE), Paragraph(f"<b>{gw_f}</b>", _CELL_RED if gw_f else _CELL_STYLE)],
        ]
        summary_table = _make_table(data, cw)
        # Color the Failed row background if any failures
        extra_styles = []
        if ap_f:
            extra_styles.append(("BACKGROUND", (1, 3), (1, 3), _LIGHT_RED))
        if sw_f:
            extra_styles.append(("BACKGROUND", (2, 3), (2, 3), _LIGHT_RED))
        if gw_f:
            extra_styles.append(("BACKGROUND", (3, 3), (3, 3), _LIGHT_RED))
        if extra_styles:
            summary_table.setStyle(TableStyle(_BASE_TABLE_STYLE + extra_styles))
        elements.append(summary_table)
        elements.append(Spacer(1, 0.5 * cm))

    # ── Site Variables ──
    tmpl_vars = result.get("template_variables", [])
    if tmpl_vars:
        elements.append(Paragraph(f"Site Variables ({len(tmpl_vars)})", heading_style))
        w = _PAGE_WIDTH
        cw = [w * 0.1, w * 0.18, w * 0.22, w * 0.25, w * 0.25]
        data = [[_ph("Validation"), _ph("Template Type"), _ph("Template Name"), _ph("Variable"), _ph("Value")]]
        for item in tmpl_vars:
            data.append([
                _p_status(item.get("status", "")),
                _p(item.get("template_type", "")),
                _p(item.get("template_name", "")),
                _p(item.get("variable", "")),
                _p(str(item.get("value", ""))),
            ])
        elements.append(_make_table(data, cw))
        elements.append(Spacer(1, 0.3 * cm))

    # ── Access Points ──
    aps = result.get("aps", [])
    if aps:
        elements.append(Paragraph(f"Access Points ({len(aps)})", heading_style))
        w = _PAGE_WIDTH
        cw = [w * 0.14, w * 0.08, w * 0.09, w * 0.11, w * 0.09, w * 0.06, w * 0.18, w * 0.25]
        data = [[_ph("Name"), _ph("Model"), _ph("Conn."), _ph("Firmware"), _ph("Eth0"), _ph("Pwr"), _ph("Config"), _ph("LLDP Neighbor")]]
        for ap in aps:
            checks = _checks_map(ap)
            lldp = ap.get("lldp_neighbor", {})
            lldp_text = f"{lldp.get('system_name', '')} ({lldp.get('port_desc', '')})" if lldp.get("system_name") else ""
            conn_status = checks.get("connection_status", {}).get("status", "info")
            power_status = checks.get("power_constrained", {}).get("status", "pass")
            data.append([
                _p(_esc(ap.get("name", ""))),
                _p(ap.get("model", "")),
                _p_status(conn_status) if conn_status != "pass" else _p(checks.get("connection_status", {}).get("value", "")),
                _p_firmware(checks.get("firmware_version", {})),
                _p(checks.get("eth0_port_speed", {}).get("value", "")),
                Paragraph("<b>Yes</b>", _CELL_ORANGE) if power_status == "warn" else _p("No"),
                _p(checks.get("config_status", {}).get("value", "")),
                _p(lldp_text),
            ])
        elements.append(_make_table(data, cw))
        elements.append(Spacer(1, 0.3 * cm))

    # ── Switches ──
    switches = result.get("switches", [])
    if switches:
        elements.append(Paragraph(f"Switches ({len(switches)})", heading_style))
        w = _PAGE_WIDTH
        cw = [w * 0.18, w * 0.15, w * 0.1, w * 0.15, w * 0.22, w * 0.2]
        data = [[_ph("Name"), _ph("Model"), _ph("Conn."), _ph("Firmware"), _ph("Config"), _ph("Cable Tests")]]
        for sw in switches:
            checks = _checks_map(sw)
            ct_count = len(sw.get("cable_tests", []))
            ct_failed = sum(1 for ct in sw.get("cable_tests", []) if ct.get("status") == "fail")
            ct_text = f"{ct_count} ports" + (f" ({ct_failed} failed)" if ct_failed else "") if ct_count else ""
            ct_style = _CELL_RED if ct_failed else _CELL_STYLE
            data.append([
                _p(_esc(sw.get("name", ""))),
                _p(sw.get("model", "")),
                _p(checks.get("connection_status", {}).get("value", "")),
                _p_firmware(checks.get("firmware_version", {})),
                _p(checks.get("config_status", {}).get("value", "")),
                Paragraph(ct_text, ct_style),
            ])
        elements.append(_make_table(data, cw))
        elements.append(Spacer(1, 0.3 * cm))

        # Per-switch details
        for sw in switches:
            checks = _checks_map(sw)
            elements.append(Paragraph(
                f"<b>{_esc(sw.get('name', '(unnamed)'))}</b> — {_esc(sw.get('model', ''))} — "
                f"Firmware: {_esc(checks.get('firmware_version', {}).get('value', ''))}",
                normal_style,
            ))

            vc = sw.get("virtual_chassis")
            if vc and vc.get("members"):
                cw_vc = [w * 0.1, w * 0.2, w * 0.2, w * 0.2, w * 0.3]
                data = [[_ph("Member"), _ph("Model"), _ph("Firmware"), _ph("VC Ports UP"), _ph("Status")]]
                for m in vc["members"]:
                    vc_up = m.get("vc_ports_up", 0)
                    data.append([
                        _p(str(m.get("member_id", ""))),
                        _p(m.get("model", "")),
                        _p(m.get("firmware", "")),
                        Paragraph(f"<b>{vc_up}</b>", _CELL_GREEN if vc_up >= 2 else _CELL_RED),
                        _p(m.get("status", "")),
                    ])
                elements.append(_make_table(data, cw_vc))

            cable_tests = sw.get("cable_tests", [])
            if cable_tests:
                cw_ct = [w * 0.15, w * 0.45, w * 0.15, w * 0.25]
                data = [[_ph("Port"), _ph("LLDP Neighbor"), _ph("Result"), _ph("Pairs")]]
                for ct in cable_tests:
                    result_status = ct.get("status", "")
                    pairs_text = ", ".join(f"{p['pair']}: {p['status']}" for p in ct.get("pairs", []))
                    data.append([
                        _p(ct.get("port", "")),
                        _p(_lldp_str(ct)),
                        Paragraph(f"<b>{result_status.upper()}</b>", _CELL_GREEN if result_status == "pass" else _CELL_RED),
                        _p(pairs_text),
                    ])
                elements.append(_make_table(data, cw_ct))

            _add_config_errors(elements, sw, normal_style)

            if sw.get("port_optics"):
                _add_optics_table(elements, sw["port_optics"], w)

            elements.append(Spacer(1, 0.3 * cm))

    # ── Gateways ──
    gateways = result.get("gateways", [])
    if gateways:
        elements.append(Paragraph(f"Gateways ({len(gateways)})", heading_style))
        w = _PAGE_WIDTH
        cw = [w * 0.15, w * 0.1, w * 0.1, w * 0.15, w * 0.22, w * 0.14, w * 0.14]
        data = [[_ph("Name"), _ph("Model"), _ph("Conn."), _ph("Firmware"), _ph("Config"), _ph("WAN"), _ph("LAN")]]
        for gw in gateways:
            checks = _checks_map(gw)
            wan_s = checks.get("wan_port_status", {}).get("status", "info")
            lan_s = checks.get("lan_port_status", {}).get("status", "info")
            data.append([
                _p(_esc(gw.get("name", ""))),
                _p(gw.get("model", "")),
                _p(checks.get("connection_status", {}).get("value", "")),
                _p_firmware(checks.get("firmware_version", {})),
                _p(checks.get("config_status", {}).get("value", "")),
                Paragraph(f"<b>{checks.get('wan_port_status', {}).get('value', '')}</b>", _CELL_GREEN if wan_s == "pass" else _CELL_RED if wan_s == "fail" else _CELL_ORANGE),
                Paragraph(f"<b>{checks.get('lan_port_status', {}).get('value', '')}</b>", _CELL_GREEN if lan_s == "pass" else _CELL_RED if lan_s == "fail" else _CELL_ORANGE),
            ])
        elements.append(_make_table(data, cw))
        elements.append(Spacer(1, 0.3 * cm))

        # Per-gateway details
        for gw in gateways:
            elements.append(Paragraph(f"<b>{_esc(gw.get('name', '(unnamed)'))}</b> — {_esc(gw.get('model', ''))}", normal_style))

            cluster = gw.get("cluster")
            if cluster and cluster.get("members"):
                cw_cl = [w * 0.15, w * 0.2, w * 0.25, w * 0.2, w * 0.2]
                data = [[_ph("Node"), _ph("Model"), _ph("Firmware"), _ph("Status"), _ph("HA State")]]
                for m in cluster["members"]:
                    m_status = m.get("status", "")
                    data.append([
                        _p(m.get("node_name", "")),
                        _p(m.get("model", "")),
                        _p(m.get("firmware", "")),
                        Paragraph(f"<b>{_esc(m_status)}</b>", _CELL_GREEN if m_status == "connected" else _CELL_RED),
                        _p(m.get("ha_state", "")),
                    ])
                elements.append(_make_table(data, cw_cl))

            wan_ports = gw.get("wan_ports", [])
            if wan_ports:
                cw_wp = [w * 0.15, w * 0.15, w * 0.1, w * 0.2, w * 0.4]
                data = [[_ph("Interface"), _ph("Name"), _ph("Status"), _ph("WAN Type"), _ph("LLDP Neighbor")]]
                for p in wan_ports:
                    data.append([_p(p.get("interface", "")), _p(p.get("name", "")), _p_updown(p.get("up", False)), _p(p.get("wan_type", "")), _p(_lldp_str(p))])
                elements.append(_make_table(data, cw_wp))

            lan_ports = gw.get("lan_ports", [])
            if lan_ports:
                cw_lp = [w * 0.15, w * 0.25, w * 0.1, w * 0.5]
                data = [[_ph("Interface"), _ph("Network"), _ph("Status"), _ph("LLDP Neighbor")]]
                for p in lan_ports:
                    data.append([_p(p.get("interface", "")), _p(p.get("network", "")), _p_updown(p.get("up", False)), _p(_lldp_str(p))])
                elements.append(_make_table(data, cw_lp))

            networks = gw.get("networks", [])
            if networks:
                cw_net = [w * 0.15, w * 0.25, w * 0.15, w * 0.45]
                data = [[_ph("Network"), _ph("Gateway IP"), _ph("DHCP"), _ph("Detail")]]
                for n in networks:
                    detail = ""
                    if n.get("dhcp_status") == "Server" and n.get("dhcp_pool"):
                        detail = f"Pool: {n['dhcp_pool']}"
                    elif n.get("dhcp_status") == "Relay" and n.get("dhcp_relay_servers"):
                        detail = f"Servers: {', '.join(n['dhcp_relay_servers'])}"
                    data.append([_p(n.get("name", "")), _p(n.get("gateway_ip", "")), _p(n.get("dhcp_status", "")), _p(detail)])
                elements.append(_make_table(data, cw_net))

            _add_config_errors(elements, gw, normal_style)

            if gw.get("port_optics"):
                _add_optics_table(elements, gw["port_optics"], w)

            elements.append(Spacer(1, 0.3 * cm))

    # ── Device Events ──
    all_device_events: list[tuple[str, str, dict]] = []  # (device_type, device_name, event)
    for dtype, dkey in [("Access Point", "aps"), ("Switch", "switches"), ("Gateway", "gateways")]:
        for dev in result.get(dkey, []):
            for ev in dev.get("events", []):
                all_device_events.append((dtype, dev.get("name", dev.get("mac", "")), ev))

    if all_device_events:
        elements.append(Paragraph(f"Device Events ({len(all_device_events)})", heading_style))
        w = _PAGE_WIDTH
        cw = [w * 0.12, w * 0.14, w * 0.22, w * 0.14, w * 0.10, w * 0.10, w * 0.18]
        data = [[_ph("Device Type"), _ph("Device"), _ph("Event"), _ph("Sub-ID"), _ph("Status"), _ph("Triggers"), _ph("Last Change")]]
        for dtype, dname, ev in all_device_events:
            status = ev.get("status", "")
            status_style = _CELL_RED if status == "triggered" else _CELL_GREEN
            ts = ev.get("last_change", 0)
            ts_str = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else ""
            data.append([
                _p(dtype),
                _p(_esc(dname)),
                _p(ev.get("display", ev.get("category", ""))),
                _p(ev.get("sub_id") or ""),
                Paragraph(f"<b>{status}</b>", status_style),
                _p(str(ev.get("trigger_count", 0))),
                _p(ts_str),
            ])
        elements.append(_make_table(data, cw))
        elements.append(Spacer(1, 0.3 * cm))

    # ── Marvis Minis ──
    marvis = result.get("marvis_minis")
    if isinstance(marvis, dict) and marvis.get("status") == "completed":
        elements.append(Paragraph("Marvis Minis Synthetic Tests", heading_style))
        s = marvis.get("summary", {})
        summary_line = (
            f"<b>{s.get('pass', 0)}</b> pass · <b>{s.get('fail', 0)}</b> fail · "
            f"<b>{s.get('warn', 0)}</b> warn · duration {marvis.get('duration_seconds', 0)}s"
        )
        elements.append(Paragraph(summary_line, normal_style))
        elements.append(Spacer(1, 0.3 * cm))

        for ap in marvis.get("ap_results", []):
            if not isinstance(ap, dict):
                continue
            ap_title = (
                f"<b>{_esc(ap.get('ap_name') or '(unnamed)')}</b> — "
                f"{_esc(ap.get('switch_name', '?'))}/{_esc(ap.get('switch_port', '?'))}"
            )
            elements.append(Paragraph(ap_title, normal_style))
            elements.append(Spacer(1, 0.1 * cm))

            header = [_ph("VLAN"), _ph("DHCP"), _ph("ARP"), _ph("DNS"), _ph("CURL")]
            data: list = [header]
            row_status_colors: list[list] = [[None] * 5]
            for vlan in ap.get("vlans", []):
                if not isinstance(vlan, dict):
                    continue
                row = [_p(str(vlan.get("vlan", "?")))]
                color_row: list = [None]
                tests_by_type = {
                    t.get("test_type"): t
                    for t in vlan.get("tests", [])
                    if isinstance(t, dict)
                }
                for tt in ("DHCP", "ARP", "DNS", "CURL"):
                    test = tests_by_type.get(tt)
                    if not test:
                        row.append(_p("—"))
                        color_row.append(None)
                        continue
                    status = test.get("status", "info")
                    row.append(_p(_esc(test.get("summary", status))))
                    color_row.append({
                        "pass": colors.HexColor("#dff5e2"),
                        "fail": colors.HexColor("#fde2e1"),
                        "warn": colors.HexColor("#fff4d6"),
                    }.get(status))
                data.append(row)
                row_status_colors.append(color_row)

            w = _PAGE_WIDTH
            cw = [w * 0.10, w * 0.225, w * 0.225, w * 0.225, w * 0.225]
            tbl = Table(data, colWidths=cw, repeatRows=1)
            extra_styles: list = []
            for r_idx, color_row in enumerate(row_status_colors):
                for c_idx, color in enumerate(color_row):
                    if color is not None:
                        extra_styles.append(("BACKGROUND", (c_idx, r_idx), (c_idx, r_idx), color))
            tbl.setStyle(TableStyle(_BASE_TABLE_STYLE + extra_styles))
            elements.append(tbl)
            elements.append(Spacer(1, 0.3 * cm))

    elif isinstance(marvis, dict) and marvis.get("status") in ("trigger_failed", "timeout"):
        elements.append(Paragraph("Marvis Minis Synthetic Tests", heading_style))
        msg = marvis.get("trigger_error") or f"Test {marvis.get('status')} after {marvis.get('duration_seconds', 0)}s"
        elements.append(Paragraph(f"<b>Status:</b> {_esc(str(marvis.get('status', '')))}", normal_style))
        elements.append(Paragraph(f"<b>Reason:</b> {_esc(str(msg))}", normal_style))
        elements.append(Spacer(1, 0.3 * cm))

    doc.build(elements)
    return buf.getvalue()


# ── CSV ──────────────────────────────────────────────────────────────────


def generate_csv_zip(report) -> bytes:
    """Generate a ZIP file containing CSV exports for each section."""
    report = _adapt_report(report)

    buf = io.BytesIO()
    result = report.result or {}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Site information
        site_info = result.get("site_info", {})
        if site_info:
            si_rows: list[dict] = []
            si_rows.append({"category": "site", "type": "name", "name": site_info.get("site_name", ""), "source": ""})
            if site_info.get("site_address"):
                si_rows.append({"category": "site", "type": "address", "name": site_info["site_address"], "source": ""})
            for g in site_info.get("site_groups", []):
                si_rows.append({"category": "site_group", "type": "", "name": g, "source": ""})
            for t in site_info.get("templates", []):
                si_rows.append({"category": "template", "type": t.get("type", ""), "name": t.get("name", ""), "source": ""})
            for w in site_info.get("org_wlans", []):
                si_rows.append({"category": "wlan", "type": "", "name": w.get("ssid", ""), "source": "org"})
            for w in site_info.get("site_wlans", []):
                si_rows.append({"category": "wlan", "type": "", "name": w.get("ssid", ""), "source": "site"})
            if si_rows:
                zf.writestr("site_info.csv", _dict_list_to_csv(si_rows, ["category", "type", "name", "source"]))

        # Site variables
        tmpl_vars = result.get("template_variables", [])
        if tmpl_vars:
            zf.writestr("site_variables.csv", _dict_list_to_csv(
                tmpl_vars, ["template_type", "template_name", "variable", "value", "defined", "status"]
            ))

        # APs
        aps = result.get("aps", [])
        if aps:
            ap_rows: list[dict] = []
            for ap in aps:
                checks = _checks_values(ap)
                checks_full = _checks_map(ap)
                fw_check = checks_full.get("firmware_version", {})
                lldp = ap.get("lldp_neighbor", {})
                ap_rows.append({
                    "name": ap.get("name", ""), "device_id": ap.get("device_id", ""),
                    "mac": ap.get("mac", ""), "model": ap.get("model", ""),
                    "connection_status": checks.get("connection_status", ""),
                    "firmware_version": checks.get("firmware_version", ""),
                    "firmware_status": fw_check.get("status", ""),
                    "firmware_recommended": fw_check.get("expected", ""),
                    "eth0_port_speed": checks.get("eth0_port_speed", ""),
                    "power_constrained": checks.get("power_constrained", ""),
                    "config_status": checks.get("config_status", ""),
                    "lldp_system_name": lldp.get("system_name", ""),
                    "lldp_port_desc": lldp.get("port_desc", ""),
                })
            zf.writestr("aps.csv", _dict_list_to_csv(ap_rows, [
                "name", "device_id", "mac", "model", "connection_status",
                "firmware_version", "firmware_status", "firmware_recommended",
                "eth0_port_speed", "power_constrained", "config_status", "lldp_system_name", "lldp_port_desc",
            ]))

        # Switches + cable tests
        switches = result.get("switches", [])
        if switches:
            sw_rows: list[dict] = []
            for sw in switches:
                checks = _checks_values(sw)
                checks_full = _checks_map(sw)
                fw_check = checks_full.get("firmware_version", {})
                sw_rows.append({
                    "name": sw.get("name", ""), "device_id": sw.get("device_id", ""),
                    "mac": sw.get("mac", ""), "model": sw.get("model", ""),
                    "connection_status": checks.get("connection_status", ""),
                    "firmware_version": checks.get("firmware_version", ""),
                    "firmware_status": fw_check.get("status", ""),
                    "firmware_recommended": fw_check.get("expected", ""),
                    "config_status": checks.get("config_status", ""),
                })
            zf.writestr("switches.csv", _dict_list_to_csv(sw_rows, [
                "name", "device_id", "mac", "model", "connection_status",
                "firmware_version", "firmware_status", "firmware_recommended", "config_status",
            ]))

            ct_rows: list[dict] = []
            for sw in switches:
                for ct in sw.get("cable_tests", []):
                    ct_rows.append({
                        "switch_name": sw.get("name", ""), "switch_mac": sw.get("mac", ""),
                        "port": ct.get("port", ""), "status": ct.get("status", ""),
                        "neighbor_system_name": ct.get("neighbor_system_name", ""),
                        "neighbor_port_desc": ct.get("neighbor_port_desc", ""),
                        "pairs": str(ct.get("pairs", [])),
                    })
            if ct_rows:
                zf.writestr("cable_tests.csv", _dict_list_to_csv(ct_rows, [
                    "switch_name", "switch_mac", "port", "status",
                    "neighbor_system_name", "neighbor_port_desc", "pairs",
                ]))

            sw_optics_rows = _optics_csv_rows(switches, "switch_name", "switch_mac")
            if sw_optics_rows:
                zf.writestr("switch_port_optics.csv", _dict_list_to_csv(sw_optics_rows, [
                    "switch_name", "switch_mac", "port_id", "media_type",
                    "xcvr_model", "xcvr_serial", "xcvr_part_number",
                    "rx_power", "rx_power_status", "tx_power", "tx_power_status",
                    "temperature", "bias_current", "voltage",
                ]))

        # Gateways + ports + networks
        gateways = result.get("gateways", [])
        if gateways:
            gw_rows: list[dict] = []
            for gw in gateways:
                checks = _checks_values(gw)
                checks_full = _checks_map(gw)
                fw_check = checks_full.get("firmware_version", {})
                gw_rows.append({
                    "name": gw.get("name", ""), "device_id": gw.get("device_id", ""),
                    "mac": gw.get("mac", ""), "model": gw.get("model", ""),
                    "connection_status": checks.get("connection_status", ""),
                    "firmware_version": checks.get("firmware_version", ""),
                    "firmware_status": fw_check.get("status", ""),
                    "firmware_recommended": fw_check.get("expected", ""),
                    "config_status": checks.get("config_status", ""),
                    "wan_port_status": checks.get("wan_port_status", ""),
                    "lan_port_status": checks.get("lan_port_status", ""),
                })
            zf.writestr("gateways.csv", _dict_list_to_csv(gw_rows, [
                "name", "device_id", "mac", "model", "connection_status",
                "firmware_version", "firmware_status", "firmware_recommended",
                "config_status", "wan_port_status", "lan_port_status",
            ]))

            wan_rows: list[dict] = []
            lan_rows: list[dict] = []
            net_rows: list[dict] = []
            for gw in gateways:
                gw_name = gw.get("name", "")
                for p in gw.get("wan_ports", []):
                    wan_rows.append({
                        "gateway_name": gw_name, "interface": p.get("interface", ""),
                        "name": p.get("name", ""), "up": "UP" if p.get("up") else "DOWN",
                        "wan_type": p.get("wan_type", ""),
                        "neighbor_system_name": p.get("neighbor_system_name", ""),
                        "neighbor_port_desc": p.get("neighbor_port_desc", ""),
                    })
                for p in gw.get("lan_ports", []):
                    lan_rows.append({
                        "gateway_name": gw_name, "interface": p.get("interface", ""),
                        "network": p.get("network", ""), "up": "UP" if p.get("up") else "DOWN",
                        "neighbor_system_name": p.get("neighbor_system_name", ""),
                        "neighbor_port_desc": p.get("neighbor_port_desc", ""),
                    })
                for n in gw.get("networks", []):
                    net_rows.append({
                        "gateway_name": gw_name, "network": n.get("name", ""),
                        "gateway_ip": n.get("gateway_ip", ""), "dhcp_status": n.get("dhcp_status", ""),
                        "dhcp_pool": n.get("dhcp_pool", ""),
                        "dhcp_relay_servers": ", ".join(n.get("dhcp_relay_servers", [])),
                    })
            if wan_rows:
                zf.writestr("gateway_wan_ports.csv", _dict_list_to_csv(wan_rows, ["gateway_name", "interface", "name", "up", "wan_type", "neighbor_system_name", "neighbor_port_desc"]))
            if lan_rows:
                zf.writestr("gateway_lan_ports.csv", _dict_list_to_csv(lan_rows, ["gateway_name", "interface", "network", "up", "neighbor_system_name", "neighbor_port_desc"]))
            if net_rows:
                zf.writestr("gateway_networks.csv", _dict_list_to_csv(net_rows, ["gateway_name", "network", "gateway_ip", "dhcp_status", "dhcp_pool", "dhcp_relay_servers"]))

            gw_optics_rows = _optics_csv_rows(gateways, "gateway_name", "gateway_mac")
            if gw_optics_rows:
                zf.writestr("gateway_port_optics.csv", _dict_list_to_csv(gw_optics_rows, [
                    "gateway_name", "gateway_mac", "port_id", "media_type",
                    "xcvr_model", "xcvr_serial", "xcvr_part_number",
                    "rx_power", "rx_power_status", "tx_power", "tx_power_status",
                    "temperature", "bias_current", "voltage",
                ]))

        # Config command errors (switches + gateways combined)
        ce_rows: list[dict] = []
        for dtype, dkey in [("switch", "switches"), ("gateway", "gateways")]:
            for dev in result.get(dkey, []):
                for err in dev.get("config_errors", []):
                    ce_rows.append({
                        "device_type": dtype,
                        "device_name": dev.get("name", ""),
                        "device_mac": dev.get("mac", ""),
                        "error": err,
                    })
        if ce_rows:
            zf.writestr("config_errors.csv", _dict_list_to_csv(ce_rows, [
                "device_type", "device_name", "device_mac", "error",
            ]))

        # Device events
        ev_rows: list[dict] = []
        for dtype, dkey in [("ap", "aps"), ("switch", "switches"), ("gateway", "gateways")]:
            for dev in result.get(dkey, []):
                for ev in dev.get("events", []):
                    ts = ev.get("last_change", 0)
                    ts_str = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else ""
                    ev_rows.append({
                        "device_type": dtype,
                        "device_name": dev.get("name", ""),
                        "device_mac": dev.get("mac", ""),
                        "category": ev.get("category", ""),
                        "display": ev.get("display", ""),
                        "sub_id": ev.get("sub_id") or "",
                        "status": ev.get("status", ""),
                        "trigger_count": ev.get("trigger_count", 0),
                        "clear_count": ev.get("clear_count", 0),
                        "last_change": ts_str,
                    })
        if ev_rows:
            zf.writestr("device_events.csv", _dict_list_to_csv(ev_rows, [
                "device_type", "device_name", "device_mac", "category", "display",
                "sub_id", "status", "trigger_count", "clear_count", "last_change",
            ]))

    return buf.getvalue()


# ── Helpers ──────────────────────────────────────────────────────────────


def _dict_list_to_csv(rows: list[dict], fields: list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


# ══════════════════════════════════════════════════════════════════════════
# Org-level exports
# ══════════════════════════════════════════════════════════════════════════


def generate_org_pdf(report) -> bytes:
    """Generate a PDF report for an org-level validation."""
    report = _adapt_report(report)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=18, spaceAfter=12)
    heading_style = ParagraphStyle("SectionHeading", parent=styles["Heading2"], fontSize=14, spaceAfter=8, spaceBefore=16)
    normal_style = styles["Normal"]

    elements: list = []
    result = report.result or {}
    org_info = result.get("org_info", {})
    sites = result.get("sites", {})
    summary = result.get("summary", {})

    # ── Title page ──
    elements.append(Paragraph("Org Validation Report", title_style))
    elements.append(Paragraph(f"Organization: {_esc(org_info.get('org_name', ''))}", normal_style))
    gen_time = report.completed_at or report.created_at
    if isinstance(gen_time, str):
        try:
            gen_time = _dt.datetime.fromisoformat(gen_time.replace("Z", "+00:00"))
        except Exception:
            gen_time = None
    gen_str = gen_time.strftime("%b %d, %Y, %H:%M") if gen_time and hasattr(gen_time, "strftime") else str(gen_time or "")
    elements.append(Paragraph(f"Generated: {gen_str}", normal_style))
    elements.append(Spacer(1, 12))

    # Device counts
    dc = org_info.get("device_counts", {})
    elements.append(Paragraph(
        f"Sites: {org_info.get('site_count', 0)} &nbsp; | &nbsp; "
        f"APs: {dc.get('aps', 0)} &nbsp; | &nbsp; "
        f"Switches: {dc.get('switches', 0)} &nbsp; | &nbsp; "
        f"Gateways: {dc.get('gateways', 0)}",
        normal_style,
    ))
    elements.append(Spacer(1, 6))

    # Summary
    elements.append(Paragraph(
        f"<font color='green'>Pass: {summary.get('pass', 0)}</font> &nbsp; "
        f"<font color='red'>Fail: {summary.get('fail', 0)}</font> &nbsp; "
        f"<font color='#ff9800'>Warn: {summary.get('warn', 0)}</font> &nbsp; "
        f"<font color='#2196f3'>Info: {summary.get('info', 0)}</font>",
        normal_style,
    ))
    elements.append(Spacer(1, 16))

    # ── Overview table ──
    elements.append(Paragraph("Site Overview", heading_style))
    overview_header = [_ph("Site"), _ph("Variables"), _ph("APs"), _ph("Switches"), _ph("Gateways")]
    overview_data = [overview_header]

    for site_id in sorted(sites.keys(), key=lambda sid: sites[sid].get("site_info", {}).get("site_name", "")):
        sr = sites[site_id]
        si = sr.get("site_info", {})
        s = sr.get("summary", {})
        ds = si.get("device_summary", {})
        # Variables status
        var_checks = sr.get("template_variables", [])
        missing = sum(1 for v in var_checks if v.get("status") == "fail")
        var_text = f"{missing} missing" if missing else "OK"
        var_style = _CELL_RED if missing else _CELL_GREEN

        overview_data.append([
            _p(si.get("site_name", site_id[:8])),
            _p(var_text, var_style),
            _p(f"{ds.get('aps', {}).get('failed', 0)}F / {ds.get('aps', {}).get('total', 0)}"),
            _p(f"{ds.get('switches', {}).get('failed', 0)}F / {ds.get('switches', {}).get('total', 0)}"),
            _p(f"{ds.get('gateways', {}).get('failed', 0)}F / {ds.get('gateways', {}).get('total', 0)}"),
        ])

    if len(overview_data) > 1:
        col_widths = [_PAGE_WIDTH * w for w in (0.35, 0.15, 0.15, 0.15, 0.2)]
        t = Table(overview_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle(_BASE_TABLE_STYLE))
        elements.append(t)

    elements.append(Spacer(1, 20))

    doc.build(elements)
    return buf.getvalue()


def generate_org_csv_zip(report) -> bytes:
    """Generate a ZIP file containing CSV exports for an org-level report."""
    report = _adapt_report(report)

    buf = io.BytesIO()
    result = report.result or {}
    sites = result.get("sites", {})
    org_info = result.get("org_info", {})

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Overview CSV
        overview_rows: list[dict] = []
        for site_id in sorted(sites.keys(), key=lambda sid: sites[sid].get("site_info", {}).get("site_name", "")):
            sr = sites[site_id]
            si = sr.get("site_info", {})
            s = sr.get("summary", {})
            ds = si.get("device_summary", {})
            var_checks = sr.get("template_variables", [])
            missing_vars = sum(1 for v in var_checks if v.get("status") == "fail")
            overview_rows.append({
                "site_name": si.get("site_name", ""),
                "site_id": site_id,
                "variables_missing": missing_vars,
                "ap_total": ds.get("aps", {}).get("total", 0),
                "ap_failed": ds.get("aps", {}).get("failed", 0),
                "sw_total": ds.get("switches", {}).get("total", 0),
                "sw_failed": ds.get("switches", {}).get("failed", 0),
                "gw_total": ds.get("gateways", {}).get("total", 0),
                "gw_failed": ds.get("gateways", {}).get("failed", 0),
                "pass": s.get("pass", 0),
                "fail": s.get("fail", 0),
                "warn": s.get("warn", 0),
                "info": s.get("info", 0),
            })
        if overview_rows:
            zf.writestr("overview.csv", _dict_list_to_csv(overview_rows, [
                "site_name", "site_id", "variables_missing",
                "ap_total", "ap_failed", "sw_total", "sw_failed",
                "gw_total", "gw_failed", "pass", "fail", "warn", "info",
            ]))

        # Template variables (all sites)
        var_rows: list[dict] = []
        for site_id, sr in sites.items():
            site_name = sr.get("site_info", {}).get("site_name", "")
            for v in sr.get("template_variables", []):
                var_rows.append({"site_name": site_name, **v})
        if var_rows:
            zf.writestr("template_variables.csv", _dict_list_to_csv(var_rows, [
                "site_name", "template_type", "template_name", "variable", "value", "defined", "status",
            ]))

        # APs (all sites)
        ap_rows: list[dict] = []
        for site_id, sr in sites.items():
            site_name = sr.get("site_info", {}).get("site_name", "")
            for ap in sr.get("aps", []):
                checks = _checks_values(ap)
                checks_full = _checks_map(ap)
                fw_check = checks_full.get("firmware_version", {})
                lldp = ap.get("lldp_neighbor", {})
                ap_rows.append({
                    "site_name": site_name,
                    "name": ap.get("name", ""), "device_id": ap.get("device_id", ""),
                    "mac": ap.get("mac", ""), "model": ap.get("model", ""),
                    "connection_status": checks.get("connection_status", ""),
                    "firmware_version": checks.get("firmware_version", ""),
                    "firmware_status": fw_check.get("status", ""),
                    "firmware_recommended": fw_check.get("expected", ""),
                    "eth0_port_speed": checks.get("eth0_port_speed", ""),
                    "power_constrained": checks.get("power_constrained", ""),
                    "config_status": checks.get("config_status", ""),
                    "lldp_system_name": lldp.get("system_name", ""),
                    "lldp_port_desc": lldp.get("port_desc", ""),
                })
        if ap_rows:
            zf.writestr("aps.csv", _dict_list_to_csv(ap_rows, [
                "site_name", "name", "device_id", "mac", "model", "connection_status",
                "firmware_version", "firmware_status", "firmware_recommended",
                "eth0_port_speed", "power_constrained", "config_status", "lldp_system_name", "lldp_port_desc",
            ]))

        # Switches (all sites)
        sw_rows: list[dict] = []
        for site_id, sr in sites.items():
            site_name = sr.get("site_info", {}).get("site_name", "")
            for sw in sr.get("switches", []):
                checks = _checks_values(sw)
                checks_full = _checks_map(sw)
                fw_check = checks_full.get("firmware_version", {})
                sw_rows.append({
                    "site_name": site_name,
                    "name": sw.get("name", ""), "device_id": sw.get("device_id", ""),
                    "mac": sw.get("mac", ""), "model": sw.get("model", ""),
                    "connection_status": checks.get("connection_status", ""),
                    "firmware_version": checks.get("firmware_version", ""),
                    "firmware_status": fw_check.get("status", ""),
                    "firmware_recommended": fw_check.get("expected", ""),
                    "config_status": checks.get("config_status", ""),
                })
        if sw_rows:
            zf.writestr("switches.csv", _dict_list_to_csv(sw_rows, [
                "site_name", "name", "device_id", "mac", "model", "connection_status",
                "firmware_version", "firmware_status", "firmware_recommended", "config_status",
            ]))

        # Gateways (all sites)
        gw_rows: list[dict] = []
        for site_id, sr in sites.items():
            site_name = sr.get("site_info", {}).get("site_name", "")
            for gw in sr.get("gateways", []):
                checks = _checks_values(gw)
                checks_full = _checks_map(gw)
                fw_check = checks_full.get("firmware_version", {})
                gw_rows.append({
                    "site_name": site_name,
                    "name": gw.get("name", ""), "device_id": gw.get("device_id", ""),
                    "mac": gw.get("mac", ""), "model": gw.get("model", ""),
                    "connection_status": checks.get("connection_status", ""),
                    "firmware_version": checks.get("firmware_version", ""),
                    "firmware_status": fw_check.get("status", ""),
                    "firmware_recommended": fw_check.get("expected", ""),
                    "config_status": checks.get("config_status", ""),
                    "wan_port_status": checks.get("wan_port_status", ""),
                    "lan_port_status": checks.get("lan_port_status", ""),
                })
        if gw_rows:
            zf.writestr("gateways.csv", _dict_list_to_csv(gw_rows, [
                "site_name", "name", "device_id", "mac", "model", "connection_status",
                "firmware_version", "firmware_status", "firmware_recommended",
                "config_status", "wan_port_status", "lan_port_status",
            ]))

        # Config errors (all sites)
        ce_rows: list[dict] = []
        for site_id, sr in sites.items():
            site_name = sr.get("site_info", {}).get("site_name", "")
            for dtype, dkey in [("switch", "switches"), ("gateway", "gateways")]:
                for dev in sr.get(dkey, []):
                    for err in dev.get("config_errors", []):
                        ce_rows.append({
                            "site_name": site_name,
                            "device_type": dtype,
                            "device_name": dev.get("name", ""),
                            "device_mac": dev.get("mac", ""),
                            "error": err,
                        })
        if ce_rows:
            zf.writestr("config_errors.csv", _dict_list_to_csv(ce_rows, [
                "site_name", "device_type", "device_name", "device_mac", "error",
            ]))

    return buf.getvalue()
