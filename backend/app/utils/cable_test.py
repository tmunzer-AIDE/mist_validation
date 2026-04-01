"""
Cable test TDR (Time Domain Reflectometry) output parser.

Parses Junos TDR output from switch cable tests into structured results.
Used by:
- Reports module (validation_service.py) for post-deployment validation
- Automation module as a Jinja2 pipe filter: ``{{ raw_output | parse_cable_test }}``
"""

import re

# ANSI escape and control character patterns
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-B0-2]")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Junos TDR output regexes
_TDR_TEST_STATUS_RE = re.compile(r"Test\s+status\s*:\s*(.+)", re.IGNORECASE)
_TDR_MDI_PAIR_RE = re.compile(r"MDI\s+pair\s*:\s*(\S+)", re.IGNORECASE)
_TDR_CABLE_STATUS_RE = re.compile(r"Cable\s+status\s*:\s*(.+)", re.IGNORECASE)
_TDR_CABLE_LENGTH_RE = re.compile(r"Cable\s+length.*?:\s*(\d+)\s*[Mm]eters?", re.IGNORECASE)

PASS_STATUSES = frozenset({"Normal", "normal", "OK", "ok", "Passed", "passed"})


def clean_terminal_text(raw: str) -> str:
    """Strip ANSI escape sequences and control characters from raw terminal output."""
    cleaned = ANSI_RE.sub("", raw)
    cleaned = CONTROL_CHAR_RE.sub("", cleaned)
    return cleaned.replace("\r", "")


def parse_tdr_output(text: str, port_id: str = "") -> dict:
    """Parse Junos TDR cable test output into structured results.

    Args:
        text: Raw or cleaned TDR output text (may contain multiple interface blocks).
        port_id: Optional port ID to isolate results for a specific interface.
            If empty, the full text is parsed as-is.

    Returns:
        dict with keys:
            status: "pass" | "fail" | "info"
            pairs: list of {"pair": str, "status": str, "length": str}
            test_status: str — the top-level Test status line value
    """
    # If port_id given, find the LAST Interface block matching it (richest data)
    target_block = text
    if port_id:
        blocks = re.split(r"(?=Interface name\s*:)", text)
        for block in blocks:
            if port_id in block:
                target_block = block  # last match wins

    # Strip any remaining ANSI sequences
    target_block = ANSI_RE.sub("", target_block)

    # Parse test status
    test_status_match = _TDR_TEST_STATUS_RE.search(target_block)
    test_status = test_status_match.group(1).strip() if test_status_match else ""

    # Parse MDI pairs
    pair_results: list[dict] = []
    current_pair = ""
    current_status = ""
    current_length = ""

    for line in target_block.splitlines():
        line_stripped = line.strip().strip("\r\x00")

        pair_match = _TDR_MDI_PAIR_RE.search(line_stripped)
        if pair_match:
            if current_pair:
                pair_results.append({"pair": current_pair, "status": current_status, "length": current_length})
            current_pair = pair_match.group(1)
            current_status = ""
            current_length = ""
            continue

        status_match = _TDR_CABLE_STATUS_RE.search(line_stripped)
        if status_match and current_pair:
            current_status = status_match.group(1).strip()
            continue

        length_match = _TDR_CABLE_LENGTH_RE.search(line_stripped)
        if length_match and current_pair:
            current_length = f"{length_match.group(1)}m"
            continue

    if current_pair:
        pair_results.append({"pair": current_pair, "status": current_status, "length": current_length})

    # Determine overall status
    if pair_results:
        overall = "pass" if all(p["status"] in PASS_STATUSES for p in pair_results) else "fail"
    elif test_status:
        overall = "pass" if test_status.startswith("Test successfully") or test_status == "Passed" else "fail"
    else:
        overall = "info"

    return {"status": overall, "pairs": pair_results, "test_status": test_status}


def parse_cable_test_filter(raw_text: str) -> dict:
    """Jinja2 filter: parse TDR cable test output.

    Usage in workflow templates::

        {{ cable_test_output | parse_cable_test }}

    Returns a dict with ``status``, ``pairs``, and ``test_status``.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        return {"status": "info", "pairs": [], "test_status": ""}
    cleaned = clean_terminal_text(raw_text)
    return parse_tdr_output(cleaned)
