import {
  Component,
  EventEmitter,
  OnInit,
  Output,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';

import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTableModule } from '@angular/material/table';
import { MatTooltipModule } from '@angular/material/tooltip';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { debounceTime, distinctUntilChanged } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { AuthInfo } from '../../app.component';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';
import { ConfirmDeleteDialogComponent } from './confirm-delete-dialog.component';

interface Site {
  id: string;
  name: string;
}

interface ReportRow {
  id: string;
  org_name: string;
  site_name: string;
  status: string;
  created_at: string;
  include_cable_tests: boolean;
}

interface SitesResponse {
  sites: Site[];
  tdr_site_ids: string[];
  tdr_group_name: string;
  tdr_group_exists: boolean;
}

interface ReportsResponse {
  reports: ReportRow[];
  total: number;
}

interface StartReportResponse {
  id: string;
}

@Component({
  selector: 'app-site-selector',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    DatePipe,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatAutocompleteModule,
    MatButtonModule,
    MatCheckboxModule,
    MatDialogModule,
    MatTableModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    StatusBadgeComponent,
  ],
  templateUrl: './site-selector.component.html',
  styleUrl: './site-selector.component.scss',
})
export class SiteSelectorComponent implements OnInit {
  authInfo = input.required<AuthInfo>();
  selectedOrg = input<{ id: string; name: string; role?: string } | null>(null);

  @Output() orgSelected = new EventEmitter<{ id: string; name: string; role?: string }>();
  @Output() reportStarted = new EventEmitter<string>();

  private api = inject(ApiService);
  private fb = inject(FormBuilder);
  private dialog = inject(MatDialog);

  orgSearchCtrl = this.fb.control('');
  siteSearchCtrl = this.fb.control({ value: '', disabled: true });
  cableTestsCtrl = this.fb.control(false);

  currentOrg = signal<{ id: string; name: string; role?: string } | null>(null);
  allSites = signal<Site[]>([]);
  sitesLoading = signal(false);
  selectedSite = signal<Site | null>(null);

  recentReports = signal<ReportRow[]>([]);
  reportsLoading = signal(false);

  tdrSiteIds = signal<string[]>([]);
  tdrGroupName = signal('');
  tdrGroupExists = signal(true);

  generating = signal(false);
  startError = signal('');

  reportColumns = ['org_name', 'site_name', 'status', 'created_at', 'action'];

  private orgQuery = toSignal(
    this.orgSearchCtrl.valueChanges.pipe(debounceTime(0), distinctUntilChanged()),
    { initialValue: '' },
  );

  filteredOrgs = computed(() => {
    const raw = this.orgQuery() ?? '';
    const q = (typeof raw === 'string' ? raw : '').toLowerCase();
    return this.authInfo().orgs.filter((o) => o.name.toLowerCase().includes(q));
  });

  private siteQuery = toSignal(
    this.siteSearchCtrl.valueChanges.pipe(debounceTime(0), distinctUntilChanged()),
    { initialValue: '' },
  );

  filteredSites = computed(() => {
    const raw = this.siteQuery() ?? '';
    const q = (typeof raw === 'string' ? raw : '').toLowerCase();
    return this.allSites().filter((s) => s.name.toLowerCase().includes(q));
  });

  canGenerate = computed(
    () => !!this.selectedSite() && (!!(this.currentOrg() || this.authInfo().orgs.length === 1)),
  );

  canWriteOrg = computed(() => {
    const org =
      this.currentOrg() ?? (this.authInfo().orgs.length === 1 ? this.authInfo().orgs[0] : null);
    const role = org?.role ?? 'read';
    return role === 'admin' || role === 'write';
  });

  siteInTdrGroup = computed(() => {
    const site = this.selectedSite();
    if (!site) return false;
    return this.tdrSiteIds().includes(site.id);
  });

  cableTestsAllowed = computed(() => {
    if (!this.canWriteOrg()) return false;
    if (!this.tdrGroupName()) return true;
    return this.siteInTdrGroup();
  });

  cableTestsDisabledReason = computed(() => {
    if (!this.canWriteOrg()) {
      return 'Cable tests require write access. Your role for this organization is read-only.';
    }
    if (!this.tdrGroupExists()) {
      const name = this.tdrGroupName();
      return `Cable tests are not available. The site group '${name}' does not exist in this organization.`;
    }
    if (!this.siteInTdrGroup()) {
      const name = this.tdrGroupName();
      return `Cable tests are not enabled for this site. Add it to the '${name}' site group in Mist to enable.`;
    }
    return '';
  });

  constructor() {
    effect(() => {
      if (!this.cableTestsAllowed()) {
        this.cableTestsCtrl.setValue(false);
        this.cableTestsCtrl.disable();
      } else {
        this.cableTestsCtrl.enable();
      }
    });

    this.siteSearchCtrl.valueChanges
      .pipe(debounceTime(0), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((val) => {
        // val can be a string (typed text) or a Site object (autocomplete selection)
        if (typeof val === 'object' && val !== null) return;
        const current = this.selectedSite();
        if (current && current.name !== val) {
          this.selectedSite.set(null);
        }
      });
  }

  ngOnInit(): void {
    const initialOrg = this.selectedOrg() ?? (this.authInfo().orgs.length === 1 ? this.authInfo().orgs[0] : null);
    if (initialOrg) {
      this.currentOrg.set(initialOrg);
      this.loadSites(initialOrg);
    }
    this.loadRecentReports();
  }

  loadSites(org?: { id: string; name: string; role?: string }): void {
    this.sitesLoading.set(true);
    this.allSites.set([]);
    this.selectedSite.set(null);
    this.siteSearchCtrl.setValue('');
    this.tdrSiteIds.set([]);
    this.tdrGroupExists.set(true);

    const auth = this.authInfo();
    const resolvedOrg = org ?? this.selectedOrg() ?? (auth.orgs.length === 1 ? auth.orgs[0] : null);
    const orgId = resolvedOrg?.id ?? '';
    this.api.get<SitesResponse>(`sites?org_id=${orgId}`).subscribe({
      next: (res) => {
        this.allSites.set(res.sites);
        this.tdrSiteIds.set(res.tdr_site_ids);
        this.tdrGroupName.set(res.tdr_group_name);
        this.tdrGroupExists.set(res.tdr_group_exists);
        this.sitesLoading.set(false);
        this.siteSearchCtrl.enable();
      },
      error: () => {
        this.sitesLoading.set(false);
      },
    });
  }

  loadRecentReports(): void {
    this.reportsLoading.set(true);
    this.api
      .get<ReportsResponse>('reports')
      .subscribe({
        next: (res) => {
          this.recentReports.set(res.reports);
          this.reportsLoading.set(false);
        },
        error: () => {
          this.reportsLoading.set(false);
        },
      });
  }

  onOrgSelected(org: { id: string; name: string }): void {
    this.currentOrg.set(org);
    this.orgSelected.emit(org);
    this.loadSites(org);
  }

  displayOrg(org: { id: string; name: string; role?: string } | null): string {
    return org?.name ?? '';
  }

  displaySite(site: Site | null): string {
    return site?.name ?? '';
  }

  generateReport(): void {
    const site = this.selectedSite();
    const auth = this.authInfo();
    const org = this.currentOrg() ?? (auth.orgs.length === 1 ? auth.orgs[0] : null);

    if (!site || !org) return;

    this.generating.set(true);
    this.startError.set('');

    this.api
      .post<StartReportResponse>(
        'reports',
        { site_id: site.id, org_id: org.id, include_cable_tests: this.cableTestsCtrl.value },
      )
      .subscribe({
        next: (res) => {
          this.generating.set(false);
          this.reportStarted.emit(res.id);
        },
        error: (err) => {
          this.generating.set(false);
          const msg = err?.error?.detail ?? err?.error?.message ?? 'Failed to start report.';
          this.startError.set(msg as string);
        },
      });
  }

  deleteReport(row: ReportRow): void {
    const label = [row.org_name, row.site_name].filter(Boolean).join(' / ') || row.id;
    const ref = this.dialog.open(ConfirmDeleteDialogComponent, {
      data: { label },
      width: '360px',
    });
    ref.afterClosed().subscribe((confirmed) => {
      if (!confirmed) return;
      this.api.delete(`reports/${row.id}`).subscribe({
        next: () => {
          this.recentReports.update((list) => list.filter((r) => r.id !== row.id));
        },
      });
    });
  }

  viewReport(id: string): void {
    this.reportStarted.emit(id);
  }
}
