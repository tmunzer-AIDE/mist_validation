import {
  Component,
  computed,
  EventEmitter,
  OnDestroy,
  OnInit,
  Output,
  inject,
  input,
  signal,
} from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { ApiService } from '../../core/services/api.service';
import { WsService } from '../../core/services/ws.service';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';
import {
  PageShellComponent,
  ShellRoute,
} from '../../shared/components/page-shell/page-shell.component';
import { RunningScreenComponent } from '../running-screen/running-screen.component';
import {
  DeviceDetailDialogComponent,
  SwitchResult,
  GatewayResult,
  DeviceCheck,
  DeviceEvent,
  DeviceResult,
} from './device-detail-dialog.component';
import {
  MarvisMatrixComponent,
  MarvisCellClick,
  MarvisLiveSnapshot,
  MarvisResult,
} from './marvis-matrix.component';
import { MarvisDetailDialogComponent } from './marvis-detail-dialog.component';
import {
  checkLabel,
  deviceTypeIcon,
  deviceTypeLabel,
  worstStatus,
} from '../../shared/utils/report-helpers';

// ---- Types ----
interface ProgressStep {
  id: string;
  label: string;
  status: string;
  message: string;
}

interface SiteInfo {
  site_name: string;
  site_address: string;
  site_groups: string[];
  templates: { type: string; name: string }[];
  org_wlans: { ssid: string }[];
  site_wlans: { ssid: string }[];
  device_summary: Record<string, { total: number; failed: number }>;
}

interface TemplateVariable {
  status: string;
  template_type: string;
  template_name: string;
  variable: string;
  value: string;
  defined?: boolean;
}

interface ReportResult {
  site_info: SiteInfo;
  template_variables: TemplateVariable[];
  aps: DeviceResult[];
  switches: SwitchResult[];
  gateways: GatewayResult[];
  marvis_minis?: MarvisResult;
  summary: { pass: number; fail: number; warn: number; info: number };
}

interface ReportResponse {
  id: string;
  org_id: string;
  site_id: string;
  site_name: string;
  scope: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: {
    overall_completed: number;
    overall_total: number;
    steps: ProgressStep[];
    eta_seconds?: number | null;
  };
  result: ReportResult | null;
  error: string | null;
  include_cable_tests: boolean;
  include_config_errors: boolean;
  include_marvis_minis: boolean;
  created_at: string;
  completed_at: string | null;
}

// ---- Helpers ----
function getCheckValue(checks: DeviceCheck[], checkId: string): string {
  return checks.find((c) => c.check === checkId)?.value ?? '';
}

function getCheckStatus(checks: DeviceCheck[], checkId: string): string {
  return checks.find((c) => c.check === checkId)?.status ?? 'info';
}

function getCheckExpected(checks: DeviceCheck[], checkId: string): string {
  return checks.find((c) => c.check === checkId)?.expected ?? '';
}

function firstIssue(checks: DeviceCheck[]): DeviceCheck | null {
  return checks.find((c) => c.status === 'fail') ?? checks.find((c) => c.status === 'warn') ?? null;
}

interface FlatDevice {
  type: 'ap' | 'switch' | 'gateway';
  name: string;
  model: string;
  mac: string;
  device_id: string;
  checks: DeviceCheck[];
  events?: DeviceEvent[];
  firmware?: string;
  // Switch-specific (only `members.length` is read in the template)
  virtual_chassis?: { members?: unknown[] } | null;
  cable_tests?: unknown[];
  config_errors?: string[];
  // Gateway-specific
  cluster?: { members?: unknown[] } | null;
}

@Component({
  selector: 'app-report-view',
  standalone: true,
  imports: [
    DatePipe,
    DecimalPipe,
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
  templateUrl: './report-view.component.html',
  styleUrl: './report-view.component.scss',
})
export class ReportViewComponent implements OnInit, OnDestroy {
  jobId = input<string | null>(null);
  reportData = input<ReportResponse | null>(null);
  embedded = input(false);
  parentLabel = input<string>('');

  @Output() back = new EventEmitter<void>();
  @Output() navigate = new EventEmitter<ShellRoute>();

  private api = inject(ApiService);
  private ws = inject(WsService);
  private dialog = inject(MatDialog);

  report = signal<ReportResponse | null>(null);
  private wsSubscription: { unsubscribe(): void } | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  marvisLive = signal<MarvisLiveSnapshot | null>(null);

  marvisData = computed<MarvisResult | MarvisLiveSnapshot | null>(() => {
    const persisted = this.report()?.result?.marvis_minis;
    if (persisted) return persisted;
    return this.marvisLive();
  });

  marvisIsLive = computed<boolean>(() => {
    return !this.report()?.result?.marvis_minis && this.marvisLive() !== null;
  });

  // View mode + filters
  vizMode = signal<'scorecard' | 'table'>('scorecard');
  tableFilter = signal<'all' | 'ap' | 'switch' | 'gateway'>('all');

  // Sub-view (main vs findings)
  subView = signal<'main' | 'findings'>('main');
  severityFilter = signal<'all' | 'fail' | 'warn'>('all');
  findingsTypeFilter = signal<'all' | 'ap' | 'switch' | 'gateway'>('all');

  // Aggregated template variables
  expandedVars = signal<Set<string>>(new Set());

  groupedVariables = computed(() => {
    const r = this.report();
    if (!r?.result?.template_variables?.length) return [];

    const groups = new Map<
      string,
      {
        variable: string;
        value: string;
        status: string;
        occurrences: { template_type: string; template_name: string; status: string }[];
      }
    >();
    for (const check of r.result.template_variables) {
      const existing = groups.get(check.variable);
      if (existing) {
        existing.occurrences.push(check);
        if (check.status === 'fail') {
          existing.status = 'fail';
        } else if (check.status === 'warn' && existing.status !== 'fail') {
          existing.status = 'warn';
        }
      } else {
        groups.set(check.variable, {
          variable: check.variable,
          value: check.value ?? '',
          status: check.status,
          occurrences: [check],
        });
      }
    }
    return Array.from(groups.values()).sort((a, b) => a.variable.localeCompare(b.variable));
  });

  // Flat device list with type discriminator
  allDevices = computed<FlatDevice[]>(() => {
    const r = this.report()?.result;
    if (!r) return [];
    return [
      ...(r.aps ?? []).map((d) => ({ ...d, type: 'ap' as const })),
      ...(r.switches ?? []).map((d) => ({ ...d, type: 'switch' as const })),
      ...(r.gateways ?? []).map((d) => ({ ...d, type: 'gateway' as const })),
    ];
  });

  filteredDevices = computed<FlatDevice[]>(() => {
    const f = this.tableFilter();
    const list = this.allDevices();
    return f === 'all' ? list : list.filter((d) => d.type === f);
  });

  // Devices not pass — for "Needs attention"
  findings = computed(() => {
    return this.allDevices()
      .map((d) => ({ device: d, status: worstStatus(d.checks ?? []), issue: firstIssue(d.checks ?? []) }))
      .filter((f) => f.status !== 'pass' && f.status !== 'info');
  });

  // 3-col by-type rollup (under score band) — single pass per type
  byTypeStats = computed(() => {
    const types: ('ap' | 'switch' | 'gateway')[] = ['ap', 'switch', 'gateway'];
    return types
      .map((t) => {
        let pass = 0, warn = 0, fail = 0, total = 0;
        for (const d of this.allDevices()) {
          if (d.type !== t) continue;
          total++;
          const s = worstStatus(d.checks ?? []);
          if (s === 'pass') pass++;
          else if (s === 'warn') warn++;
          else if (s === 'fail') fail++;
        }
        return { type: t, label: deviceTypeLabel(t), icon: deviceTypeIcon(t), total, pass, warn, fail };
      })
      .filter((s) => s.total > 0);
  });

  // Per-check coverage across all devices
  checkCoverage = computed(() => {
    const devices = this.allDevices();
    const ids = new Set<string>();
    for (const d of devices) for (const c of d.checks ?? []) ids.add(c.check);
    return Array.from(ids)
      .map((id) => {
        const applicable = devices.filter((d) => (d.checks ?? []).some((c) => c.check === id));
        const pass = applicable.filter((d) => getCheckStatus(d.checks ?? [], id) === 'pass').length;
        const warn = applicable.filter((d) => getCheckStatus(d.checks ?? [], id) === 'warn').length;
        const fail = applicable.filter((d) => getCheckStatus(d.checks ?? [], id) === 'fail').length;
        return { check: id, label: checkLabel(id), total: applicable.length, pass, warn, fail };
      })
      .filter((c) => c.total > 0)
      .sort((a, b) => b.fail + b.warn - (a.fail + a.warn) || a.label.localeCompare(b.label));
  });

  // Site-wide events from all devices
  siteEvents = computed(() => {
    const out: { device: FlatDevice; event: DeviceEvent }[] = [];
    for (const d of this.allDevices()) {
      for (const e of d.events ?? []) {
        out.push({ device: d, event: e });
      }
    }
    return out.sort((a, b) => (b.event.last_change ?? 0) - (a.event.last_change ?? 0));
  });

  activeEventCount = computed(
    () => this.siteEvents().filter((e) => e.event.status === 'triggered').length,
  );

  // Counts for filter tabs
  filterCounts = computed(() => {
    const list = this.allDevices();
    return {
      all: list.length,
      ap: list.filter((d) => d.type === 'ap').length,
      switch: list.filter((d) => d.type === 'switch').length,
      gateway: list.filter((d) => d.type === 'gateway').length,
    };
  });

  // Detailed findings — one row per failing/warning check on a device
  detailedFindings = computed(() => {
    const out: { device: FlatDevice; check: DeviceCheck; severity: string }[] = [];
    for (const d of this.allDevices()) {
      for (const c of d.checks ?? []) {
        if (c.status === 'fail' || c.status === 'warn') {
          out.push({ device: d, check: c, severity: c.status });
        }
      }
    }
    return out.sort((a, b) => {
      if (a.severity !== b.severity) return a.severity === 'fail' ? -1 : 1;
      return a.device.name.localeCompare(b.device.name);
    });
  });

  filteredDetailedFindings = computed(() => {
    const sev = this.severityFilter();
    const type = this.findingsTypeFilter();
    return this.detailedFindings().filter(
      (f) =>
        (sev === 'all' || f.severity === sev) && (type === 'all' || f.device.type === type),
    );
  });

  findingsCounts = computed(() => {
    const all = this.detailedFindings();
    const fail = all.filter((f) => f.severity === 'fail').length;
    const warn = all.filter((f) => f.severity === 'warn').length;
    const devices = new Set(all.map((f) => f.device.device_id)).size;
    return { all: all.length, fail, warn, devices };
  });

  findingTypeCounts = computed(() => {
    const list = this.detailedFindings();
    return {
      all: list.length,
      ap: list.filter((f) => f.device.type === 'ap').length,
      switch: list.filter((f) => f.device.type === 'switch').length,
      gateway: list.filter((f) => f.device.type === 'gateway').length,
    };
  });

  openFindings(): void {
    this.subView.set('findings');
  }

  backToMain(): void {
    this.subView.set('main');
  }

  setSeverityFilter(s: 'all' | 'fail' | 'warn'): void {
    this.severityFilter.set(s);
  }

  setFindingsTypeFilter(t: 'all' | 'ap' | 'switch' | 'gateway'): void {
    this.findingsTypeFilter.set(t);
  }

  findingDescription(check: DeviceCheck): string {
    return check.value || (check.status === 'fail' ? 'Check failed' : 'Check warning');
  }

  findingRecommendation(checkId: string): string {
    const map: Record<string, string> = {
      name_defined: 'Set a meaningful device name in Mist',
      firmware_version: 'Schedule a firmware upgrade window',
      connection_status: 'Check uplink + power; escalate to TAC if persistent',
      config_status: 'Re-push configuration from the Mist UI',
      eth0_port_speed: 'Verify uplink switch port speed/duplex',
      power_constrained: 'Move the AP to an 802.3at PoE+ source',
      lldp_neighbor: 'Verify LLDP is enabled on the upstream switch',
      optics_health: 'Replace the transceiver and inspect fiber',
      cable_tests: 'Re-test cable, replace if faulty',
      config_errors: 'Review configuration in Mist; remove invalid blocks',
      wan_port_status: 'Verify the upstream provider link',
      lan_port_status: 'Check cable + admin port state',
      member_present: 'Reseat / reconnect missing VC member',
      firmware_match: 'Align VC member firmware with primary',
      vc_ports_up: 'Check VC interconnect cabling',
      node_connected: 'Check HA cluster sync; verify both nodes',
    };
    return map[checkId] ?? 'Investigate in the Mist UI';
  }

  setVizMode(m: 'scorecard' | 'table'): void {
    this.vizMode.set(m);
  }

  setTableFilter(f: 'all' | 'ap' | 'switch' | 'gateway'): void {
    this.tableFilter.set(f);
  }

  deviceTypeIcon = deviceTypeIcon;
  deviceTypeLabel = deviceTypeLabel;
  checkLabel = checkLabel;

  formatVar(name: string): string {
    return '{{' + name + '}}';
  }

  findingHint(f: { issue: DeviceCheck | null }): string {
    if (!f.issue) return '—';
    const id = f.issue.check;
    const v = f.issue.value || '';
    return `${checkLabel(id)}${v ? ' · ' + v : ''}`;
  }

  toggleVariable(varName: string): void {
    this.expandedVars.update((set) => {
      const next = new Set(set);
      if (next.has(varName)) next.delete(varName);
      else next.add(varName);
      return next;
    });
  }

  isVarExpanded(varName: string): boolean {
    return this.expandedVars().has(varName);
  }

  statusIcon(status: string): string {
    switch (status) {
      case 'pass': return 'check_circle';
      case 'fail': return 'cancel';
      case 'warn': return 'warning';
      default: return 'info';
    }
  }

  // Expose helpers to template
  getCheckValue = getCheckValue;
  getCheckStatus = getCheckStatus;
  getCheckExpected = getCheckExpected;
  deviceOverallStatus = worstStatus;

  lldpDisplay(device: DeviceResult & { lldp_neighbor?: { system_name?: string; port_desc?: string } }): string {
    const lldp = device.lldp_neighbor;
    if (!lldp) return '';
    const parts = [lldp.system_name, lldp.port_desc].filter(Boolean);
    return parts.join(' / ') || '';
  }

  progressPercent(): number {
    const p = this.report()?.progress;
    if (!p || p.overall_total === 0) return 0;
    return Math.round((p.overall_completed / p.overall_total) * 100);
  }

  deviceSummaryEntries(): { key: string; value: { total: number; failed: number } }[] {
    const summary = this.report()?.result?.site_info?.device_summary;
    if (!summary) return [];
    return Object.entries(summary).map(([key, value]) => ({ key, value }));
  }

  stepIcon(status: string): string {
    switch (status) {
      case 'completed':
        return 'check_circle';
      case 'running':
        return 'hourglass_empty';
      case 'failed':
        return 'cancel';
      default:
        return 'radio_button_unchecked';
    }
  }

  stepIconClass(status: string): string {
    switch (status) {
      case 'completed':
        return 'done';
      case 'running':
        return 'running';
      case 'failed':
        return 'failed';
      default:
        return 'pending';
    }
  }

  cableTestStatus = worstStatus;

  openEventCount(device: { events?: DeviceEvent[] }): number {
    return (device.events ?? []).filter((e) => e.status === 'triggered').length;
  }

  totalDevices(): number {
    const r = this.report()?.result;
    if (!r) return 0;
    return (r.aps?.length ?? 0) + (r.switches?.length ?? 0) + (r.gateways?.length ?? 0);
  }

  scoreValue(): number {
    const r = this.report()?.result;
    if (!r) return 0;
    const total = r.summary.pass + r.summary.fail + r.summary.warn;
    if (total === 0) return 0;
    return Math.round((r.summary.pass / total) * 100);
  }

  scoreStatus(): 'pass' | 'warn' | 'fail' {
    const s = this.scoreValue();
    if (s >= 90) return 'pass';
    if (s >= 75) return 'warn';
    return 'fail';
  }

  scoreLabel(): string {
    const s = this.scoreStatus();
    return s === 'pass' ? 'Production ready' : s === 'warn' ? 'Action recommended' : 'Action required';
  }

  onShellNavigate(route: ShellRoute): void {
    if (route === 'site_selector') {
      this.back.emit();
    } else {
      this.navigate.emit(route);
    }
  }

  ngOnInit(): void {
    const data = this.reportData();
    if (data) {
      // Dual-mode: report data provided directly (e.g., from org report drill-down)
      this.report.set(data);
      return;
    }
    this.loadReport(true);
    this.subscribeWs();
  }

  ngOnDestroy(): void {
    const jid = this.jobId();
    if (jid) {
      const channel = `report:${jid}`;
      this.ws.unsubscribe(channel);
    }
    this.wsSubscription?.unsubscribe();
    this.stopPolling();
  }

  private startPolling(): void {
    this.pollTimer = setInterval(() => {
      const status = this.report()?.status;
      if (status === 'pending' || status === 'running') {
        this.loadReport();
      } else {
        this.stopPolling();
      }
    }, 5000);
  }

  private stopPolling(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  loadReport(initial = false): void {
    const jid = this.jobId();
    if (!jid) return;
    this.api.get<ReportResponse>(`reports/${jid}`).subscribe({
      next: (r) => {
        this.report.set(r);
        if (r.status === 'completed' || r.status === 'failed') {
          this.stopPolling();
        } else if (initial) {
          this.startPolling();
        }
      },
      error: () => this.stopPolling(),
    });
  }

  private subscribeWs(): void {
    const jid = this.jobId();
    if (!jid) return;
    const channel = `report:${jid}`;
    this.ws.subscribe(channel);
    this.wsSubscription = this.ws.channel$(channel).subscribe((msg) => {
      const type = msg['type'] as string;

      if (type === 'report_progress') {
        const current = this.report();
        const data = msg['data'] as {
          status?: ReportResponse['status'];
          overall_completed?: number;
          overall_total?: number;
          steps?: ProgressStep[];
          eta_seconds?: number | null;
        };
        if (current && data) {
          this.report.set({
            ...current,
            status: data.status ?? current.status,
            progress: {
              overall_completed: data.overall_completed ?? current.progress?.overall_completed ?? 0,
              overall_total: data.overall_total ?? current.progress?.overall_total ?? 0,
              steps: data.steps ?? current.progress?.steps ?? [],
              eta_seconds: data.eta_seconds,
            },
          });
        }
      }

      if (type === 'report_complete') {
        this.loadReport();
      }

      if (type === 'report_failed') {
        const current = this.report();
        if (current) {
          this.report.set({
            ...current,
            status: 'failed',
            error: (msg['error'] as string) ?? 'Validation failed.',
          });
        }
      }

      if (type === 'marvis_progress') {
        const data = msg['data'] as MarvisLiveSnapshot;
        if (data) {
          this.marvisLive.set(data);
        }
      }
    });
  }

  openDeviceDetail(
    device: DeviceResult | SwitchResult | GatewayResult | FlatDevice,
    type: 'ap' | 'switch' | 'gateway',
  ): void {
    this.dialog.open(DeviceDetailDialogComponent, {
      data: { device: device as DeviceResult | SwitchResult | GatewayResult, type },
      width: '700px',
      maxWidth: '92vw',
      height: '100vh',
      maxHeight: '100vh',
      position: { right: '0', top: '0' },
      panelClass: 'mv-side-drawer',
      autoFocus: false,
    });
  }

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

  private exportFile(path: string, filename: string): void {
    this.api.getBlob(path).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
      },
    });
  }

  exportPdf(): void {
    const jid = this.jobId();
    if (!jid) return;
    const siteName = this.report()?.site_name ?? 'report';
    this.exportFile(`reports/${jid}/export/pdf`, `${siteName}-validation.pdf`);
  }

  exportCsv(): void {
    const jid = this.jobId();
    if (!jid) return;
    const siteName = this.report()?.site_name ?? 'report';
    this.exportFile(`reports/${jid}/export/csv`, `${siteName}-validation.zip`);
  }
}
