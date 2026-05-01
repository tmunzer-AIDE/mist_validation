# Marvis Minis — Design

**Date:** 2026-05-01
**Status:** Approved (brainstorming complete; awaiting plan)
**Owner:** tmunzer@juniper.net

## 1. What this is

Marvis Minis is Juniper Mist's AI-native digital twin: it triggers synthetic
DHCP/ARP/DNS/CURL tests across active VLANs from a few APs in a site, even
without real users present. This spec adds it as a new opt-in check inside the
post-deployment validation flow so the report can surface VLAN-level
reachability problems alongside the existing AP/switch/gateway checks.

The end result the user sees: an opt-in toggle on the site selector, a step in
the live progress screen during the ~4-minute test, and — in the final report —
a top-level **Synthetic tests** section rendering an AP × VLAN matrix with
per-test drill-downs.

## 2. Scope

In scope (v1):
- Site-scope reports only.
- Bundled into the existing `run_post_deployment_validation` flow as one
  optional step.
- New top-level result block, AP × VLAN matrix UI with per-cell drill-down,
  PDF + CSV export.
- Score impact: every VLAN sub-test counts toward the site overall score.

Out of scope (v1, but the entry-point signature is built so this is a follow-up,
not a rewrite):
- Org-wide reports including Marvis Minis.
- A "Marvis only" run mode independent of the validation report.
- Subscription pre-flight detection — surface trigger errors instead.
- AP-presence pre-flight check — surface trigger errors instead.

## 3. Architecture

### 3.1 New backend module

`backend/app/services/marvis_service.py` — single file, ~300 lines, one public
entry point:

```python
async def run_marvis_minis(
    session: APISession,
    org_id: str,
    site_id: str,
    tracker: _ProgressTracker,
    progress_callback,
    job_id: str,
) -> dict:
    """
    Trigger a synthetic test, poll until complete, parse + score the results,
    and broadcast live progress. Returns the marvis_minis result block ready
    to be stored under result["marvis_minis"].
    """
```

The signature accepts `org_id` and `site_id` separately so a future
`run_org_validation` can call it per-site in a loop without the entry point
having to change.

The module also owns:
- The trigger function (wraps `session.mist_post`).
- The poll loop with the 15s/5s cadence and 8-minute hard timeout.
- The parser that converts the raw `test_details` payload into the result
  schema in §5.
- The status-rollup helper used by both the parser and `_compute_summary`.

### 3.2 Integration into `validation_service.py`

- Append `("marvis_minis", "Marvis Minis")` to the `_STEPS` list, **after**
  `("config_errors", "Config Command Errors")`. This makes it the last step,
  so any earlier results are visible/persisted before the 4-minute Marvis run
  starts.
- Add `include_marvis_minis: bool = False` to
  `run_post_deployment_validation`'s parameters and to `ReportCreateRequest`.
- Inside the function, after the `config_errors` block:
  - If `include_marvis_minis`:
    `result["marvis_minis"] = await marvis_service.run_marvis_minis(...)`
  - Else: `start_step + complete_step` with message "Skipped (opt-in)".
- `_compute_summary` is updated to walk
  `result["marvis_minis"].ap_results[].vlans[].tests[]` and add each test's
  `pass`/`fail`/`warn` status into the site-level totals. `not_validated` and
  `not_tested` are excluded.

### 3.3 ETA model

The cable-test pattern is reused. `_ProgressTracker` gains:
- `_marvis_finish_at: float | None` (monotonic seconds)
- `start_marvis_phase()` — anchors `_marvis_finish_at = now + 240`
- `_eta_seconds()` adds, when the marvis step is `pending` or `running`:
  - pending → `240` (predicted)
  - running with anchor → `max(0, _marvis_finish_at - now)`
  - running without anchor → `240` (transient guard, same idea as cable test)

Per-step API cost registered as ~1 (just the trigger). Polls don't count
against the budget weight — wall-clock dominates, same justification as cable
tests' 25 s/port.

### 3.4 Pre-flight gating

Mirroring `include_cable_tests` in `api/reports.py`:
- If `include_marvis_minis and not session.can_write(org_id)` → HTTP 403
  with body `{"detail": "Marvis Minis requires write access to the
  organization."}`.

No subscription check, no AP-presence check upfront. Both surface as a clean
trigger-failure result in §4.2.

## 4. Backend data flow

### 4.1 Trigger

```
POST /api/v1/sites/{site_id}/synthetic_test     body: {}
→ session.mist_post(uri, body={})
```

On a 2xx response, `resp.data["id"]` is the `test_id`.

### 4.2 Trigger failure handling

If the POST returns non-2xx:
- Log the response status and body.
- Set the tracker step status to `"failed"` with the API error message.
- Return:
  ```
  {
    "status": "trigger_failed",
    "trigger_error": "<status_code> <reason>",
    "test_id": null,
    "ap_results": [],
    "summary": {"pass": 0, "fail": 0, "warn": 0}
  }
  ```
- `run_post_deployment_validation` continues normally with the rest of the
  report. The report is still marked `completed`. The Marvis section in the UI
  shows the trigger error prominently.

### 4.3 Poll loop

```
GET /api/v1/labs/orgs/{org_id}/synthetic_test
    ?q=test_details&view=table&site_id={site_id}&test_id={test_id}
→ session.mist_get(uri, query={...})
```

Cadence: `await asyncio.sleep(15)` for the first 120 s of polling, then
`await asyncio.sleep(5)`. Implemented as a loop with `start_time = monotonic()`
and the interval picked from `15 if (now - start_time) < 120 else 5`.

Termination — any of the following ends the loop and treats the snapshot as
final:
- `data["test_status"] == "completed"` (present in the final-response example).
- `data["result"] in ("success", "failure")` AND `data["progress"] >= 100`
  (defensive fallback; some responses may not include `test_status`).

(The in-progress example has `result == "in_progress"` and a partial
`progress`, so this exits the loop only when one of the above triggers.)

Hard timeout: 8 minutes from trigger. On timeout, store
`{"status": "timeout", "test_id": ..., "ap_results": <last_snapshot>, ...}`,
mark the step `failed`, but the surrounding report still completes.

### 4.4 Per-poll side effects

After every successful poll, in order:

1. **Update the tracker step message** with a human summary. Walk
   `test_details` for the first AP whose `end is null`; in that AP's `vlans`,
   find the first VLAN with non-empty `connectivity` whose last entry has the
   most-recent state. Format:
   `"Testing 1/2 APs · DNT-NTR-APB VLAN 8 — CURL"`. If all APs have `end != null`,
   the test is finishing up: `"Finalizing results…"`.

2. **Broadcast `marvis_progress`** on the `report:{job_id}` channel — see §6.

The standard `report_progress` broadcast also fires (because `update_step`
calls `_broadcast`), so older clients still see the step ticking.

### 4.5 test_id persistence

The `test_id` is stored on the marvis result block. PCAP URLs in the response
are pre-signed and embedded in the result; the `test_id` itself enables future
features like a "Re-run this test in Mist UI" deep-link.

## 5. Result schema

`result["marvis_minis"]` (top-level, peer to `aps`/`switches`/`gateways`):

```json
{
  "status": "completed",
  "test_id": "0a00966a-1713-c6f6-6f01-d09d9efc1ff2",
  "duration_seconds": 208,
  "started_at": 1777569296,
  "result": "failure",
  "summary": { "pass": 12, "fail": 5, "warn": 0 },
  "trigger_error": null,
  "ap_results": [
    {
      "ap_mac": "04a92439fb75",
      "ap_name": "DNT-NTR-APB",
      "switch_name": "DNT-NTR-SWB-3",
      "switch_port": "mge-0/0/0",
      "vlans": [
        {
          "vlan": 7,
          "status": "pass",
          "has_pcap": false,
          "pcap_url": null,
          "tests": [
            {
              "test_type": "DHCP",
              "status": "pass",
              "summary": "10.3.7.18/24 · 9ms",
              "detail": { /* raw test_detail object */ }
            },
            { "test_type": "ARP",  "status": "pass", "summary": "10.3.7.9 · 54ms",  "detail": {} },
            { "test_type": "DNS",  "status": "pass", "summary": "4 URLs · avg 27ms", "detail": {} },
            { "test_type": "CURL", "status": "pass", "summary": "4/4 · avg 76ms",   "detail": {} }
          ]
        },
        {
          "vlan": 20,
          "status": "fail",
          "has_pcap": true,
          "pcap_url": "https://storage.googleapis.com/...",
          "tests": [
            { "test_type": "DHCP", "status": "fail", "summary": "DHCP Unresponsive · retry 4", "detail": {} },
            { "test_type": "ARP",  "status": "info", "summary": "Not validated",                "detail": {} },
            { "test_type": "DNS",  "status": "info", "summary": "Not validated",                "detail": {} },
            { "test_type": "CURL", "status": "info", "summary": "Not validated",                "detail": {} }
          ]
        }
      ]
    }
  ]
}
```

### 5.1 Per-test status mapping

| Raw `test_status` | Schema `status` | Counts toward score? |
|-------------------|-----------------|----------------------|
| `success`         | `pass`          | yes                  |
| `failure`         | `fail`          | yes                  |
| `not_validated`   | `info`          | no                   |

VLANs with empty `connectivity[]` in the final response → vlan-level
`status: "not_tested"`, no per-test rows generated; not counted.

### 5.2 Per-test summary strings

The parser produces a one-line `summary` per test, derived from `test_detail`:

| test_type | success summary                                       | failure summary                              |
|-----------|-------------------------------------------------------|----------------------------------------------|
| DHCP      | `<ip>/<prefix> · <ack_latency>ms`                     | `<state> · retry N` or `<state>`             |
| ARP       | `<gw_ip> · <latency>ms`                               | `Failed: <error>`                            |
| DNS       | `N URLs · avg <Xms>` (avg of `latency` over `urls[]`) | `<failed_count>/<total> failed`              |
| CURL      | `<ok>/<total> · avg <Xms>`                            | `<failed_count>/<total> · <error excerpt>`   |

The full raw `test_detail` is preserved alongside under `detail` so the UI
can expand it without a second API call.

### 5.3 VLAN-level rollup

`vlan.status` = worst of its tests' statuses (excluding `info`/`not_tested`).
Order: `fail > warn > pass > info > not_tested`. `warn` is reserved (not yet
emitted by the v1 parser, but the schema supports it for future signals like
"latency above threshold").

Edge case — if a VLAN has tests but every one is `info` (all `not_validated`):
this is theoretically possible only if the AP did not even attempt the first
test. The rollup falls through to `info` in that case, and the matrix cell
renders the same as `not_tested` (a muted dash, no drill-down). Practically,
DHCP either succeeds or fails; cascading-info-only is not expected.

### 5.4 Score impact (Q2 answer A — every VLAN-test counts)

`_compute_summary` adds, for each AP × VLAN × test in `ap_results`:
- `status == "pass"` → `summary.pass += 1`
- `status == "fail"` → `summary.fail += 1`
- `status == "warn"` → `summary.warn += 1`
- `status == "info"` (i.e. `not_validated`) → not counted
- vlan-level `not_tested` → no test rows, nothing counted

In the example payload (2 APs × 5 VLANs × 4 tests = 40 max), `not_validated`
and `not_tested` reduce the effective denominator to ~32.

## 6. WebSocket broadcasts

### 6.1 New broadcast type

```json
{
  "type": "marvis_progress",
  "channel": "report:<job_id>",
  "data": {
    "test_id": "0a00966a-...",
    "progress": 23.4,
    "ap_count_done": 1,
    "ap_count_total": 2,
    "ap_results": [ /* same shape as the final result, partially filled */ ]
  }
}
```

Sent on the existing `report:{job_id}` channel after every poll. Volume is
bounded at ~32 messages per test (8 polls during the first 120s @ 15s, then
~24 polls @ 5s during the remaining ~120s).

### 6.2 Channel auth

Unchanged — `ws.py`'s existing `_authorize_channel("report:...")` ownership
check covers this. No new channel needed.

### 6.3 Backwards compatibility

Older clients that ignore unknown `type` fields are unaffected — they still
receive the `report_progress` events triggered by `tracker.update_step`, which
include the marvis step's status and message text. The only missing
functionality for those clients is the live matrix.

## 7. Frontend integration

### 7.1 Site selector — opt-in toggle

`features/site-selector/site-selector.component.{ts,html}`:
- New `marvisMinisCtrl: FormControl<boolean>`.
- A new opt-in card in the same group as Cable Diagnostics:
  - Title: "Marvis Minis"
  - Badges: `HEAVY`, `~4 min`
  - Hint: "Synthetic DHCP/ARP/DNS/CURL tests across active VLANs · per-site only"
  - Disabled with tooltip "Requires write access to the organization" if the
    session is read-only.
- Submitted via the new `include_marvis_minis` flag on the
  `ReportCreateRequest` payload.

### 7.2 Running screen — phase grouping only

`features/running-screen/running-screen.component.ts`:
- Add `marvis_minis: 'Diagnostics'` to `PHASE_BY_STEP`. The step then groups
  with `cable_tests` and `config_errors` under "Diagnostics".
- The step's `message` field (updated by the backend per-poll) renders as-is.
  No live matrix on the running screen — kept lean per the agreed UX.

### 7.3 Report view — new top-level section

`features/report-view/report-view.component.{ts,html}`:
- New top-level "Synthetic tests (Marvis Minis)" section that renders if
  `report().result?.marvis_minis` exists OR `marvisLive()` exists.
- WS handler in `subscribeWs()` adds a third
  `if (type === 'marvis_progress')` branch that updates a new
  `marvisLive = signal<MarvisLiveSnapshot | null>(null)`.
- The matrix component reads from `report().result?.marvis_minis ?? marvisLive()`,
  so it transitions seamlessly from live → final.
- A `marvis_minis` filter chip is added to the existing findings sub-view so
  Marvis-specific failures are reachable from the unified findings list.

### 7.4 New child component — `marvis-matrix.component.ts`

A new standalone component under `features/report-view/`:
- **Inputs:** `data: MarvisResult | MarvisLiveSnapshot`.
- **Layout:** an AP × VLAN grid. Rows = APs (sorted by `ap_name`), columns =
  VLAN IDs sorted ascending. Header row: VLAN IDs. First column: AP name +
  switch port chip.
- **Cells:**
  - `pass`: green check.
  - `warn`: amber warning.
  - `fail`: red cancel + small "PCAP" indicator if `has_pcap`.
  - `not_tested` / `info`: muted dash, click is inert (no drawer opens).
  - During live mode: cells with no data yet render a muted spinner.
- **Click a cell** → opens a side-drawer dialog (`mv-side-drawer` panel class,
  matching the existing pattern) with:
  - 4 sub-test rows (DHCP / ARP / DNS / CURL), each with the status badge,
    summary string, and a collapsible "Raw response" panel showing the
    `detail` JSON with monospace formatting.
  - PCAP download link rendered as `<a href="..." target="_blank"
    rel="noopener">Download .pcap</a>` if `pcap_url` is present (mirrors the
    PR review feedback in commit 8d50d52 about external links).

### 7.5 PDF export

`backend/app/services/export_service.py`, `generate_pdf`:
- New section "Marvis Minis Synthetic Tests" rendered as a ReportLab table:
  one row per AP × VLAN, columns = AP, VLAN, DHCP, ARP, DNS, CURL. Status
  cells are colored by worst status. Skipped if `result.marvis_minis` is
  missing or `status != "completed"`.

### 7.6 CSV export

`generate_csv_zip`:
- New file `marvis_minis.csv` in the zip. Columns:
  `ap_name, ap_mac, switch_name, switch_port, vlan, test_type, status, summary, has_pcap`.
  One row per `{ap, vlan, test_type}`. Empty (header row only) if no Marvis
  section exists in the result.

### 7.7 Validation reference page

`features/validation-reference/`:
- New entry describing Marvis Minis: what the four sub-tests check, what
  their pass criteria are, and what the score impact is. Surfaces the same
  reference card pattern as the existing entries.

## 8. Error handling summary

| Failure mode                        | Surface where                                | Report status |
|-------------------------------------|----------------------------------------------|---------------|
| User read-only → toggle on          | API returns 403 from `/api/reports`          | not created   |
| Trigger POST returns 4xx/5xx        | step `failed`, marvis result `trigger_failed`| `completed`   |
| Poll returns malformed JSON         | retry once on next interval; if 3 in a row, treat as timeout | `completed`   |
| Hard timeout (8 min)                | step `failed`, marvis result `timeout`       | `completed`   |
| Other steps fail before marvis      | normal `_run_post_deployment_validation` error path | `failed`      |

The principle: a Marvis-specific problem never fails the whole report. The
report-level status remains tied to the overall validation flow. Marvis
failures live inside the marvis result block.

## 9. Future work (not in this spec)

- Org-wide Marvis Minis: invoke `run_marvis_minis` once per site in a loop
  inside `run_org_validation`, with a top-level `org_marvis_summary` block.
- Subscription pre-flight detection so the toggle can disable proactively
  rather than waiting for trigger failure.
- "Re-run failed VLAN only" deep-link from the matrix to the Mist UI.
- Latency thresholds → `warn` status (the schema already supports it, the
  parser does not yet emit it).

## 10. Open questions

None at this time. All previously open questions resolved during
brainstorming on 2026-05-01.
