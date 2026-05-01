import {
  Component,
  EventEmitter,
  OnInit,
  Output,
  computed,
  inject,
  signal,
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { toSignal } from '@angular/core/rxjs-interop';
import { debounceTime, distinctUntilChanged } from 'rxjs';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ApiService } from '../../core/services/api.service';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';
import {
  PageShellComponent,
  ShellRoute,
} from '../../shared/components/page-shell/page-shell.component';
import { ConfirmDeleteDialogComponent } from '../../shared/components/confirm-delete-dialog/confirm-delete-dialog.component';

interface ReportRow {
  id: string;
  org_id: string;
  org_name: string;
  site_id: string;
  site_name: string;
  scope: string;
  status: string;
  result: {
    summary?: { pass: number; fail: number; warn: number; info: number };
    aps?: unknown[];
    switches?: unknown[];
    gateways?: unknown[];
    org_info?: { device_counts?: { aps: number; switches: number; gateways: number } };
  } | null;
  created_at: string;
  completed_at: string | null;
}

interface ReportsResponse {
  reports: ReportRow[];
  total: number;
}

@Component({
  selector: 'app-reports',
  standalone: true,
  imports: [
    DatePipe,
    ReactiveFormsModule,
    MatButtonModule,
    MatDialogModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    StatusBadgeComponent,
    PageShellComponent,
  ],
  templateUrl: './reports.component.html',
  styleUrl: './reports.component.scss',
})
export class ReportsComponent implements OnInit {
  @Output() navigate = new EventEmitter<ShellRoute>();
  @Output() openReport = new EventEmitter<{ id: string; scope: string }>();

  private api = inject(ApiService);
  private fb = inject(FormBuilder);
  private dialog = inject(MatDialog);

  reports = signal<ReportRow[]>([]);
  loading = signal(false);

  searchCtrl = this.fb.nonNullable.control('');

  private searchQuery = toSignal(
    this.searchCtrl.valueChanges.pipe(debounceTime(120), distinctUntilChanged()),
    { initialValue: '' },
  );

  filtered = computed(() => {
    const q = this.searchQuery().toLowerCase().trim();
    const list = this.reports();
    if (!q) return list;
    return list.filter(
      (r) =>
        r.id.toLowerCase().includes(q) ||
        r.site_name?.toLowerCase().includes(q) ||
        r.org_name?.toLowerCase().includes(q),
    );
  });

  ngOnInit(): void {
    this.loadReports();
  }

  loadReports(): void {
    this.loading.set(true);
    this.api.get<ReportsResponse>('reports').subscribe({
      next: (res) => {
        // Most recent first
        const sorted = [...res.reports].sort((a, b) =>
          (b.created_at ?? '').localeCompare(a.created_at ?? ''),
        );
        this.reports.set(sorted);
        this.loading.set(false);
      },
      error: () => {
        this.reports.set([]);
        this.loading.set(false);
      },
    });
  }

  scopeLabel(r: ReportRow): string {
    if (r.scope === 'org') return r.org_name || 'Organization-wide';
    return r.site_name || '—';
  }

  scopeIcon(r: ReportRow): string {
    return r.scope === 'org' ? 'business' : 'location_on';
  }

  deviceCount(r: ReportRow): number | null {
    const res = r.result;
    if (!res) return null;
    if (r.scope === 'org' && res.org_info?.device_counts) {
      const d = res.org_info.device_counts;
      return (d.aps ?? 0) + (d.switches ?? 0) + (d.gateways ?? 0);
    }
    return (
      (res.aps?.length ?? 0) + (res.switches?.length ?? 0) + (res.gateways?.length ?? 0)
    );
  }

  score(r: ReportRow): number | null {
    const s = r.result?.summary;
    if (!s) return null;
    const total = s.pass + s.warn + s.fail;
    if (total === 0) return null;
    return Math.round((s.pass / total) * 100);
  }

  scoreColor(score: number | null): string {
    if (score === null) return 'muted';
    if (score >= 90) return 'pass';
    if (score >= 75) return 'warn';
    return 'fail';
  }

  duration(r: ReportRow): string {
    if (!r.created_at || !r.completed_at) return '—';
    const start = new Date(r.created_at).getTime();
    const end = new Date(r.completed_at).getTime();
    if (isNaN(start) || isNaN(end) || end < start) return '—';
    const sec = Math.round((end - start) / 1000);
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${s.toString().padStart(2, '0')}s`;
  }

  view(r: ReportRow): void {
    this.openReport.emit({ id: r.id, scope: r.scope || 'site' });
  }

  delete(r: ReportRow, ev: Event): void {
    ev.stopPropagation();
    const label = [r.org_name, r.site_name].filter(Boolean).join(' / ') || r.id;
    const ref = this.dialog.open(ConfirmDeleteDialogComponent, {
      data: { label },
      width: '360px',
    });
    ref.afterClosed().subscribe((confirmed) => {
      if (!confirmed) return;
      this.api.delete(`reports/${r.id}`).subscribe({
        next: () => {
          this.reports.update((list) => list.filter((x) => x.id !== r.id));
        },
      });
    });
  }

  onShellNavigate(route: ShellRoute): void {
    this.navigate.emit(route);
  }
}
