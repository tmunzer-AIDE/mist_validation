import {
  Component,
  EventEmitter,
  OnInit,
  Output,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
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

interface Site {
  id: string;
  name: string;
}

interface ReportRow {
  id: string;
  site_name: string;
  status: string;
  created_at: string;
  include_cable_tests: boolean;
}

interface SitesResponse {
  sites: Site[];
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
  selectedOrg = input<{ id: string; name: string } | null>(null);

  @Output() orgSelected = new EventEmitter<{ id: string; name: string }>();
  @Output() reportStarted = new EventEmitter<string>();

  private api = inject(ApiService);
  private fb = inject(FormBuilder);

  orgSearchCtrl = this.fb.control('');
  siteSearchCtrl = this.fb.control({ value: '', disabled: true });
  cableTestsCtrl = this.fb.control(false);

  currentOrg = signal<{ id: string; name: string } | null>(null);
  allSites = signal<Site[]>([]);
  sitesLoading = signal(false);
  selectedSite = signal<Site | null>(null);

  recentReports = signal<ReportRow[]>([]);
  reportsLoading = signal(false);

  generating = signal(false);
  startError = signal('');

  reportColumns = ['site_name', 'status', 'created_at', 'action'];

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

  constructor() {
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

  loadSites(org?: { id: string; name: string }): void {
    this.sitesLoading.set(true);
    this.allSites.set([]);
    this.selectedSite.set(null);
    this.siteSearchCtrl.setValue('');

    const auth = this.authInfo();
    const resolvedOrg = org ?? this.selectedOrg() ?? (auth.orgs.length === 1 ? auth.orgs[0] : null);
    const orgId = resolvedOrg?.id ?? '';
    this.api.get<SitesResponse>(`sites?org_id=${orgId}`).subscribe({
      next: (res) => {
        this.allSites.set(res.sites);
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

  displayOrg(org: { id: string; name: string } | null): string {
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

  deleteReport(id: string): void {
    this.api.delete(`reports/${id}`).subscribe({
      next: () => {
        this.recentReports.update((list) => list.filter((r) => r.id !== id));
      },
    });
  }

  viewReport(id: string): void {
    this.reportStarted.emit(id);
  }
}
