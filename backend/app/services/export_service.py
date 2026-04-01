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


def _p_updown(is_up: bool) -> Paragraph:
    """Colored UP/DOWN cell."""
    if is_up:
        return Paragraph("<b>UP</b>", _CELL_GREEN)
    return Paragraph("<b>DOWN</b>", _CELL_RED)


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
            checks = {c["check"]: c for c in ap.get("checks", [])}
            lldp = ap.get("lldp_neighbor", {})
            lldp_text = f"{lldp.get('system_name', '')} ({lldp.get('port_desc', '')})" if lldp.get("system_name") else ""
            conn_status = checks.get("connection_status", {}).get("status", "info")
            power_status = checks.get("power_constrained", {}).get("status", "pass")
            data.append([
                _p(_esc(ap.get("name", ""))),
                _p(ap.get("model", "")),
                _p_status(conn_status) if conn_status != "pass" else _p(checks.get("connection_status", {}).get("value", "")),
                _p(checks.get("firmware_version", {}).get("value", "")),
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
            checks = {c["check"]: c for c in sw.get("checks", [])}
            ct_count = len(sw.get("cable_tests", []))
            ct_failed = sum(1 for ct in sw.get("cable_tests", []) if ct.get("status") == "fail")
            ct_text = f"{ct_count} ports" + (f" ({ct_failed} failed)" if ct_failed else "") if ct_count else ""
            ct_style = _CELL_RED if ct_failed else _CELL_STYLE
            data.append([
                _p(_esc(sw.get("name", ""))),
                _p(sw.get("model", "")),
                _p(checks.get("connection_status", {}).get("value", "")),
                _p(checks.get("firmware_version", {}).get("value", "")),
                _p(checks.get("config_status", {}).get("value", "")),
                Paragraph(ct_text, ct_style),
            ])
        elements.append(_make_table(data, cw))
        elements.append(Spacer(1, 0.3 * cm))

        # Per-switch details
        for sw in switches:
            checks = {c["check"]: c for c in sw.get("checks", [])}
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
            elements.append(Spacer(1, 0.3 * cm))

    # ── Gateways ──
    gateways = result.get("gateways", [])
    if gateways:
        elements.append(Paragraph(f"Gateways ({len(gateways)})", heading_style))
        w = _PAGE_WIDTH
        cw = [w * 0.15, w * 0.1, w * 0.1, w * 0.15, w * 0.22, w * 0.14, w * 0.14]
        data = [[_ph("Name"), _ph("Model"), _ph("Conn."), _ph("Firmware"), _ph("Config"), _ph("WAN"), _ph("LAN")]]
        for gw in gateways:
            checks = {c["check"]: c for c in gw.get("checks", [])}
            wan_s = checks.get("wan_port_status", {}).get("status", "info")
            lan_s = checks.get("lan_port_status", {}).get("status", "info")
            data.append([
                _p(_esc(gw.get("name", ""))),
                _p(gw.get("model", "")),
                _p(checks.get("connection_status", {}).get("value", "")),
                _p(checks.get("firmware_version", {}).get("value", "")),
                _p(checks.get("config_status", {}).get("value", "")),
                Paragraph(f"<b>{checks.get('wan_port_status', {}).get('value', '')}</b>", _CELL_GREEN if wan_s == "pass" else _CELL_RED if wan_s == "fail" else _CELL_ORANGE),
                Paragraph(f"<b>{checks.get('lan_port_status', {}).get('value', '')}</b>", _CELL_GREEN if lan_s == "pass" else _CELL_RED if lan_s == "fail" else _CELL_ORANGE),
            ])
        elements.append(_make_table(data, cw))
        elements.append(Spacer(1, 0.3 * cm))

        # Per-gateway details
        for gw in gateways:
            elements.append(Paragraph(f"<b>{_esc(gw.get('name', '(unnamed)'))}</b> — {_esc(gw.get('model', ''))}", normal_style))

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
                checks = {c["check"]: c.get("value", "") for c in ap.get("checks", [])}
                lldp = ap.get("lldp_neighbor", {})
                ap_rows.append({
                    "name": ap.get("name", ""), "device_id": ap.get("device_id", ""),
                    "mac": ap.get("mac", ""), "model": ap.get("model", ""),
                    "connection_status": checks.get("connection_status", ""),
                    "firmware_version": checks.get("firmware_version", ""),
                    "eth0_port_speed": checks.get("eth0_port_speed", ""),
                    "power_constrained": checks.get("power_constrained", ""),
                    "config_status": checks.get("config_status", ""),
                    "lldp_system_name": lldp.get("system_name", ""),
                    "lldp_port_desc": lldp.get("port_desc", ""),
                })
            zf.writestr("aps.csv", _dict_list_to_csv(ap_rows, [
                "name", "device_id", "mac", "model", "connection_status", "firmware_version",
                "eth0_port_speed", "power_constrained", "config_status", "lldp_system_name", "lldp_port_desc",
            ]))

        # Switches + cable tests
        switches = result.get("switches", [])
        if switches:
            sw_rows: list[dict] = []
            for sw in switches:
                checks = {c["check"]: c.get("value", "") for c in sw.get("checks", [])}
                sw_rows.append({
                    "name": sw.get("name", ""), "device_id": sw.get("device_id", ""),
                    "mac": sw.get("mac", ""), "model": sw.get("model", ""),
                    "connection_status": checks.get("connection_status", ""),
                    "firmware_version": checks.get("firmware_version", ""),
                    "config_status": checks.get("config_status", ""),
                })
            zf.writestr("switches.csv", _dict_list_to_csv(sw_rows, [
                "name", "device_id", "mac", "model", "connection_status", "firmware_version", "config_status",
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

        # Gateways + ports + networks
        gateways = result.get("gateways", [])
        if gateways:
            gw_rows: list[dict] = []
            for gw in gateways:
                checks = {c["check"]: c.get("value", "") for c in gw.get("checks", [])}
                gw_rows.append({
                    "name": gw.get("name", ""), "device_id": gw.get("device_id", ""),
                    "mac": gw.get("mac", ""), "model": gw.get("model", ""),
                    "connection_status": checks.get("connection_status", ""),
                    "firmware_version": checks.get("firmware_version", ""),
                    "config_status": checks.get("config_status", ""),
                    "wan_port_status": checks.get("wan_port_status", ""),
                    "lan_port_status": checks.get("lan_port_status", ""),
                })
            zf.writestr("gateways.csv", _dict_list_to_csv(gw_rows, [
                "name", "device_id", "mac", "model", "connection_status", "firmware_version",
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

    return buf.getvalue()


# ── Helpers ──────────────────────────────────────────────────────────────


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _dict_list_to_csv(rows: list[dict], fields: list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()
