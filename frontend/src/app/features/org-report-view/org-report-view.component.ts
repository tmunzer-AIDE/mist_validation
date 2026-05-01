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
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MvToggleComponent } from '../../shared/components/mv-toggle/mv-toggle.component';
import { ApiService } from '../../core/services/api.service';
import { WsService } from '../../core/services/ws.service';
import {
  PageShellComponent,
  ShellRoute,
} from '../../shared/components/page-shell/page-shell.component';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';
import { ReportViewComponent } from '../report-view/report-view.component';
import { RunningScreenComponent } from '../running-screen/running-screen.component';
import {
  checkLabel,
  deviceTypeIcon,
  deviceTypeLabel,
  worstStatus,
} from '../../shared/utils/report-helpers';

interface ProgressStep {
  id: string;
  label: string;
  status: string;
  message: string;
}

interface OrgReportResponse {
  id: string;
  org_id: string;
  org_name: string;
  site_id: string;
  site_name: string;
  scope: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: { overall_completed: number; overall_total: number; steps: ProgressStep[] };
  result: OrgReportResult | null;
  error: string | null;
  include_cable_tests: boolean;
  include_config_errors: boolean;
  created_at: string;
  completed_at: string | null;
}

interface OrgDevice {
  device_id: string;
  name: string;
  mac: string;
  model: string;
  checks: { check: string; status: string; value?: string; expected?: string }[];
  events?: { status: string }[];
  [key: string]: unknown;
}

interface SiteResult {
  site_info: {
    site_name: string;
    site_address: string;
    site_groups: string[];
    templates: { type: string; name: string }[];
    org_wlans: { ssid: string }[];
    site_wlans: { ssid: string }[];
    device_summary: Record<string, { total: number; failed: number }>;
  };
  template_variables: {
    variable: string;
    status: string;
    value: string;
    defined: boolean;
    template_type?: string;
    template_name?: string;
  }[];
  aps: OrgDevice[];
  switches: OrgDevice[];
  gateways: OrgDevice[];
  summary: { pass: number; fail: number; warn: number; info: number };
}

interface OrgReportResult {
  org_info: {
    org_name: string;
    org_id: string;
    site_count: number;
    device_counts: { aps: number; switches: number; gateways: number };
  };
  sites: Record<string, SiteResult>;
  summary: { pass: number; fail: number; warn: number; info: number };
}

interface SiteEntry {
  site_id: string;
  site_name: string;
  variables_total: number;
  variables_defined: number;
  variables_missing: number;
  variables_status: string;
  ap_counts: { fail: number; warn: number; pass: number; info: number; total: number };
  sw_counts: { fail: number; warn: number; pass: number; info: number; total: number };
  gw_counts: { fail: number; warn: number; pass: number; info: number; total: number };
  has_devices: boolean;
  worst_status: string;
}

type StatusFilter = 'fail' | 'warn' | 'pass' | 'info' | 'all';

function countCheckStatuses(devices: unknown[]): { fail: number; warn: number; pass: number; info: number; total: number } {
  const counts = { fail: 0, warn: 0, pass: 0, info: 0, total: devices.length };
  for (const dev of devices) {
    const d = dev as Record<string, unknown>;
    const checks = (d['checks'] as { status: string }[]) ?? [];
    const cableTests = (d['cable_tests'] as { status: string }[]) ?? [];
    const allChecks = [...checks, ...cableTests];
    if (allChecks.some((c) => c.status === 'fail')) counts.fail++;
    else if (allChecks.some((c) => c.status === 'warn')) counts.warn++;
    else counts.pass++;
  }
  return counts;
}

function siteWorstStatus(entry: SiteEntry): string {
  const hasFail = entry.ap_counts.fail > 0 || entry.sw_counts.fail > 0 || entry.gw_counts.fail > 0 || entry.variables_status === 'fail';
  if (hasFail) return 'fail';
  const hasWarn = entry.ap_counts.warn > 0 || entry.sw_counts.warn > 0 || entry.gw_counts.warn > 0;
  if (hasWarn) return 'warn';
  return 'pass';
}

function siteMatchesFilter(entry: SiteEntry, status: StatusFilter): boolean {
  if (status === 'all') return true;
  return siteWorstStatus(entry) === status;
}

@Component({
  selector: 'app-org-report-view',
  standalone: true,
  imports: [
    DatePipe,
    FormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatProgressSpinnerModule,
    MatSelectModule,
    MatTooltipModule,
    MvToggleComponent,
    PageShellComponent,
    StatusBadgeComponent,
    ReportViewComponent,
    RunningScreenComponent,
  ],
  templateUrl: './org-report-view.component.html',
  styleUrl: './org-report-view.component.scss',
})
export class OrgReportViewComponent implements OnInit, OnDestroy {
  jobId = input.required<string>();

  @Output() back = new EventEmitter<void>();
  @Output() navigate = new EventEmitter<ShellRoute>();

  private api = inject(ApiService);
  private ws = inject(WsService);

  report = signal<OrgReportResponse | null>(null);
  private wsSubscription: { unsubscribe(): void } | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  statusFilter = signal<StatusFilter>('all');
  searchQuery = signal('');
  showEmptySites = signal(false);
  sortColumn = signal('site_name');
  sortDirection = signal<'asc' | 'desc'>('asc');

  selectedSiteId = signal<string | null>(null);
  selectedSiteReport = signal<unknown | null>(null);

  siteEntries = computed<SiteEntry[]>(() => {
    const r = this.report();
    if (!r?.result?.sites) return [];
    const entries: SiteEntry[] = [];
    for (const [siteId, sr] of Object.entries(r.result.sites)) {
      // Count unique variable names (each var may appear multiple times for different templates)
      const uniqueVars = new Set(sr.template_variables.map((v) => v.variable));
      const uniqueMissing = new Set(sr.template_variables.filter((v) => v.status === 'fail').map((v) => v.variable));
      const varsTotal = uniqueVars.size;
      const varsMissing = uniqueMissing.size;
      const varsDefined = varsTotal - varsMissing;
      const entry: SiteEntry = {
        site_id: siteId,
        site_name: sr.site_info.site_name || siteId.substring(0, 8),
        variables_total: varsTotal,
        variables_defined: varsDefined,
        variables_missing: varsMissing,
        variables_status: varsMissing > 0 ? 'fail' : 'pass',
        ap_counts: countCheckStatuses(sr.aps),
        sw_counts: countCheckStatuses(sr.switches),
        gw_counts: countCheckStatuses(sr.gateways),
        has_devices: sr.aps.length + sr.switches.length + sr.gateways.length > 0,
        worst_status: 'pass',
      };
      entry.worst_status = siteWorstStatus(entry);
      entries.push(entry);
    }
    return entries;
  });

  filteredSites = computed(() => {
    const filter = this.statusFilter();
    const query = this.searchQuery().toLowerCase();
    const col = this.sortColumn();
    const dir = this.sortDirection();
    const showEmpty = this.showEmptySites();

    let entries = this.siteEntries().filter((e) => siteMatchesFilter(e, filter));
    if (!showEmpty) {
      entries = entries.filter((e) => e.has_devices);
    }
    if (query) {
      entries = entries.filter((e) => e.site_name.toLowerCase().includes(query));
    }

    entries.sort((a, b) => {
      let cmp = 0;
      if (col === 'site_name') cmp = a.site_name.localeCompare(b.site_name);
      else if (col === 'variables') cmp = a.variables_missing - b.variables_missing;
      else if (col === 'aps') cmp = b.ap_counts.fail - a.ap_counts.fail;
      else if (col === 'switches') cmp = b.sw_counts.fail - a.sw_counts.fail;
      else if (col === 'gateways') cmp = b.gw_counts.fail - a.gw_counts.fail;
      return dir === 'asc' ? cmp : -cmp;
    });

    return entries;
  });

  // ── Viz mode (Scorecard / Sites) ──
  vizMode = signal<'scorecard' | 'sites'>('scorecard');
  setVizMode(m: 'scorecard' | 'sites'): void {
    this.vizMode.set(m);
  }

  // ── Score (org-wide) ──
  scoreValue = computed(() => {
    const s = this.report()?.result?.summary;
    if (!s) return 0;
    const total = s.pass + s.warn + s.fail;
    if (total === 0) return 0;
    return Math.round((s.pass / total) * 100);
  });

  scoreStatus = computed<'pass' | 'warn' | 'fail'>(() => {
    const v = this.scoreValue();
    if (v >= 90) return 'pass';
    if (v >= 75) return 'warn';
    return 'fail';
  });

  scoreLabel = computed(() => {
    const s = this.scoreStatus();
    return s === 'pass'
      ? 'Production ready'
      : s === 'warn'
        ? 'Action recommended'
        : 'Action required';
  });

  // ── Flat device list across all sites ──
  allDevices = computed(() => {
    const sites = this.report()?.result?.sites ?? {};
    const out: {
      type: 'ap' | 'switch' | 'gateway';
      site_id: string;
      site_name: string;
      checks: { check: string; status: string; value?: string; expected?: string }[];
    }[] = [];
    for (const [siteId, sr] of Object.entries(sites)) {
      const siteName = sr.site_info?.site_name ?? siteId.slice(0, 8);
      for (const d of sr.aps ?? []) {
        out.push({ ...d, type: 'ap', site_id: siteId, site_name: siteName });
      }
      for (const d of sr.switches ?? []) {
        out.push({ ...d, type: 'switch', site_id: siteId, site_name: siteName });
      }
      for (const d of sr.gateways ?? []) {
        out.push({ ...d, type: 'gateway', site_id: siteId, site_name: siteName });
      }
    }
    return out;
  });

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
        return {
          type: t,
          label: deviceTypeLabel(t),
          icon: deviceTypeIcon(t),
          total,
          pass,
          warn,
          fail,
        };
      })
      .filter((s) => s.total > 0);
  });

  problematicSites = computed(() => {
    return this.siteEntries().filter((e) => e.has_devices && e.worst_status !== 'pass');
  });

  // Per-check coverage across all devices in all sites
  checkCoverage = computed(() => {
    const devices = this.allDevices();
    const ids = new Set<string>();
    for (const d of devices) for (const c of d.checks ?? []) ids.add(c.check);
    return Array.from(ids)
      .map((id) => {
        const applicable = devices.filter((d) =>
          (d.checks ?? []).some((c) => c.check === id),
        );
        const status = (devChecks: OrgDevice['checks']) =>
          devChecks.find((c) => c.check === id)?.status ?? 'info';
        const pass = applicable.filter((d) => status(d.checks) === 'pass').length;
        const warn = applicable.filter((d) => status(d.checks) === 'warn').length;
        const fail = applicable.filter((d) => status(d.checks) === 'fail').length;
        return {
          check: id,
          label: checkLabel(id),
          total: applicable.length,
          pass,
          warn,
          fail,
        };
      })
      .filter((c) => c.total > 0)
      .sort((a, b) => b.fail + b.warn - (a.fail + a.warn) || a.label.localeCompare(b.label));
  });

  // Aggregate template variables across all sites — each variable once, with site/template locations
  groupedVariables = computed(() => {
    const sites = this.report()?.result?.sites ?? {};
    const groups = new Map<
      string,
      {
        variable: string;
        value: string;
        status: string;
        occurrences: { site_name: string; template_type?: string; template_name?: string; status: string }[];
      }
    >();
    for (const [siteId, sr] of Object.entries(sites)) {
      const siteName = sr.site_info?.site_name ?? siteId.slice(0, 8);
      for (const v of sr.template_variables ?? []) {
        const existing = groups.get(v.variable);
        const occ = {
          site_name: siteName,
          template_type: v.template_type,
          template_name: v.template_name,
          status: v.status,
        };
        if (existing) {
          existing.occurrences.push(occ);
          if (v.status === 'fail') existing.status = 'fail';
          else if (v.status === 'warn' && existing.status !== 'fail') existing.status = 'warn';
        } else {
          groups.set(v.variable, {
            variable: v.variable,
            value: v.value ?? '',
            status: v.status,
            occurrences: [occ],
          });
        }
      }
    }
    return [...groups.values()].sort((a, b) => a.variable.localeCompare(b.variable));
  });

  formatVar(name: string): string {
    return '{{' + name + '}}';
  }

  siteFindingHint(s: SiteEntry): string {
    const fail = s.ap_counts.fail + s.sw_counts.fail + s.gw_counts.fail;
    const warn = s.ap_counts.warn + s.sw_counts.warn + s.gw_counts.warn;
    const parts: string[] = [];
    if (fail) parts.push(`${fail} failing`);
    if (warn) parts.push(`${warn} warning${warn === 1 ? '' : 's'}`);
    if (s.variables_missing) parts.push(`${s.variables_missing} undefined var${s.variables_missing === 1 ? '' : 's'}`);
    return parts.join(' · ') || 'Review details';
  }

  progressPercent(): number {
    const p = this.report()?.progress;
    if (!p || p.overall_total === 0) return 0;
    return Math.round((p.overall_completed / p.overall_total) * 100);
  }

  stepIcon(status: string): string {
    switch (status) {
      case 'completed': return 'check_circle';
      case 'running': return 'hourglass_empty';
      case 'failed': return 'cancel';
      default: return 'radio_button_unchecked';
    }
  }

  stepIconClass(status: string): string {
    switch (status) {
      case 'completed': return 'done';
      case 'running': return 'running';
      case 'failed': return 'failed';
      default: return 'pending';
    }
  }

  sort(column: string): void {
    if (this.sortColumn() === column) {
      this.sortDirection.set(this.sortDirection() === 'asc' ? 'desc' : 'asc');
    } else {
      this.sortColumn.set(column);
      this.sortDirection.set('asc');
    }
  }

  selectSite(siteId: string): void {
    const r = this.report();
    if (!r?.result?.sites?.[siteId]) return;
    const sr = r.result.sites[siteId];
    // Build a ReportResponse-shaped object for ReportViewComponent
    this.selectedSiteReport.set({
      id: r.id,
      org_id: r.org_id,
      org_name: r.org_name,
      site_id: siteId,
      site_name: sr.site_info.site_name,
      scope: 'site',
      status: 'completed',
      progress: { overall_completed: 0, overall_total: 0, steps: [] },
      result: sr,
      error: null,
      include_cable_tests: r.include_cable_tests,
      include_config_errors: r.include_config_errors,
      created_at: r.created_at,
      completed_at: r.completed_at,
    });
    this.selectedSiteId.set(siteId);
  }

  backToOverview(): void {
    this.selectedSiteId.set(null);
    this.selectedSiteReport.set(null);
  }

  totalDevices(): number {
    const c = this.report()?.result?.org_info?.device_counts;
    if (!c) return 0;
    return (c.aps ?? 0) + (c.switches ?? 0) + (c.gateways ?? 0);
  }

  onShellNavigate(route: ShellRoute): void {
    if (route === 'site_selector') {
      this.back.emit();
    } else {
      this.navigate.emit(route);
    }
  }

  ngOnInit(): void {
    this.loadReport(true);
    this.subscribeWs();
  }

  ngOnDestroy(): void {
    const channel = `report:${this.jobId()}`;
    this.ws.unsubscribe(channel);
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
    this.api.get<OrgReportResponse>(`reports/${this.jobId()}`).subscribe({
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
    const channel = `report:${this.jobId()}`;
    this.ws.subscribe(channel);
    this.wsSubscription = this.ws.channel$(channel).subscribe((msg) => {
      const type = msg['type'] as string;
      if (type === 'report_progress') {
        const current = this.report();
        const data = msg['data'] as {
          status?: OrgReportResponse['status'];
          overall_completed?: number;
          overall_total?: number;
          steps?: ProgressStep[];
        };
        if (current && data) {
          this.report.set({
            ...current,
            status: data.status ?? current.status,
            progress: {
              overall_completed: data.overall_completed ?? current.progress?.overall_completed ?? 0,
              overall_total: data.overall_total ?? current.progress?.overall_total ?? 0,
              steps: data.steps ?? current.progress?.steps ?? [],
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
    const name = this.report()?.org_name ?? 'report';
    this.exportFile(`reports/${this.jobId()}/export/pdf`, `${name}-validation.pdf`);
  }

  exportCsv(): void {
    const name = this.report()?.org_name ?? 'report';
    this.exportFile(`reports/${this.jobId()}/export/csv`, `${name}-validation.zip`);
  }
}
