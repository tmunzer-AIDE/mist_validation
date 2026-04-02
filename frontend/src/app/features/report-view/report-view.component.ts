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
import { DatePipe, TitleCasePipe } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';

import { MatDividerModule } from '@angular/material/divider';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTableModule } from '@angular/material/table';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDialog } from '@angular/material/dialog';
import { ApiService } from '../../core/services/api.service';
import { WsService } from '../../core/services/ws.service';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';
import {
  DeviceDetailDialogComponent,
  SwitchResult,
  GatewayResult,
  DeviceCheck,
  DeviceEvent,
  DeviceResult,
} from './device-detail-dialog.component';

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
  template: string;
  variable: string;
  value: string;
}

interface ReportResult {
  site_info: SiteInfo;
  template_variables: TemplateVariable[];
  aps: DeviceResult[];
  switches: SwitchResult[];
  gateways: GatewayResult[];
  summary: { pass: number; fail: number; warn: number; info: number };
}

interface ReportResponse {
  id: string;
  org_id: string;
  site_id: string;
  site_name: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: { overall_completed: number; overall_total: number; steps: ProgressStep[] };
  result: ReportResult | null;
  error: string | null;
  include_cable_tests: boolean;
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

function worstStatus(items: { status: string }[]): string {
  if (!items.length) return 'info';
  if (items.some((i) => i.status === 'fail')) return 'fail';
  if (items.some((i) => i.status === 'warn')) return 'warn';
  return 'pass';
}

@Component({
  selector: 'app-report-view',
  standalone: true,
  imports: [
    DatePipe,
    TitleCasePipe,
    MatButtonModule,
    MatCardModule,
    MatDividerModule,
    MatExpansionModule,
    MatIconModule,
    MatProgressBarModule,
    MatProgressSpinnerModule,
    MatTableModule,
    MatTooltipModule,
    StatusBadgeComponent,
  ],
  templateUrl: './report-view.component.html',
  styleUrl: './report-view.component.scss',
})
export class ReportViewComponent implements OnInit, OnDestroy {
  jobId = input.required<string>();


  @Output() back = new EventEmitter<void>();

  private api = inject(ApiService);
  private ws = inject(WsService);
  private dialog = inject(MatDialog);

  report = signal<ReportResponse | null>(null);
  private wsSubscription: { unsubscribe(): void } | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  // Aggregated template variables
  expandedVars = signal<Set<string>>(new Set());

  groupedVariables = computed(() => {
    const r = this.report();
    if (!r?.result?.template_variables?.length) return [];

    const groups = new Map<string, { variable: string; value: string; status: string; occurrences: any[] }>();
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

  sortedAps = computed(() => {
    const aps = this.report()?.result?.aps ?? [];
    return [...aps].sort((a, b) => a.name.localeCompare(b.name));
  });

  sortedSwitches = computed(() => {
    const switches = this.report()?.result?.switches ?? [];
    return [...switches].sort((a, b) => a.name.localeCompare(b.name));
  });

  sortedGateways = computed(() => {
    const gateways = this.report()?.result?.gateways ?? [];
    return [...gateways].sort((a, b) => a.name.localeCompare(b.name));
  });

  // Table column definitions
  apColumns = ['status', 'name', 'model', 'connection', 'firmware', 'eth0_speed', 'power', 'config', 'lldp', 'events'];
  switchColumns = ['status', 'name', 'model', 'type', 'connection', 'firmware', 'config', 'cable_tests', 'optics', 'events'];
  gatewayColumns = ['status', 'name', 'model', 'type', 'connection', 'firmware', 'config', 'wan', 'lan', 'optics', 'events'];

  // Expose helpers to template
  getCheckValue = getCheckValue;
  getCheckStatus = getCheckStatus;
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

  openEventCount(device: DeviceResult): number {
    return (device.events ?? []).filter((e) => e.status === 'triggered').length;
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
    this.api.get<ReportResponse>(`reports/${this.jobId()}`).subscribe({
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
          status?: ReportResponse['status'];
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

  openDeviceDetail(device: DeviceResult | SwitchResult | GatewayResult, type: 'ap' | 'switch' | 'gateway'): void {
    this.dialog.open(DeviceDetailDialogComponent, {
      data: { device, type },
      width: '720px',
      maxWidth: '95vw',
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
    const siteName = this.report()?.site_name ?? 'report';
    this.exportFile(`reports/${this.jobId()}/export/pdf`, `${siteName}-validation.pdf`);
  }

  exportCsv(): void {
    const siteName = this.report()?.site_name ?? 'report';
    this.exportFile(`reports/${this.jobId()}/export/csv`, `${siteName}-validation.zip`);
  }
}
