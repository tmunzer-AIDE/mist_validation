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
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { debounceTime, distinctUntilChanged } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { AuthInfo } from '../../app.component';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';
import { PageShellComponent } from '../../shared/components/page-shell/page-shell.component';

interface Site {
  id: string;
  name: string;
}

interface ReportRow {
  id: string;
  org_id: string;
  org_name: string;
  site_id: string;
  site_name: string;
  scope: string;
  status: string;
  created_at: string;
  include_cable_tests: boolean;
  include_config_errors: boolean;
}

interface BudgetInfo {
  allowed: boolean;
  reason: string;
  available: number;
  estimated: number;
  config_errors_allowed: boolean;
  config_errors_reason: string;
  site_count: number;
  device_counts: { ap: number; switch: number; gateway: number };
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
    MatAutocompleteModule,
    MatButtonModule,
    MatCheckboxModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    StatusBadgeComponent,
    PageShellComponent,
  ],
  templateUrl: './site-selector.component.html',
  styleUrl: './site-selector.component.scss',
})
export class SiteSelectorComponent implements OnInit {
  authInfo = input.required<AuthInfo>();
  selectedOrg = input<{ id: string; name: string; role?: string } | null>(null);

  @Output() orgSelected = new EventEmitter<{ id: string; name: string; role?: string }>();
  @Output() reportStarted = new EventEmitter<{ id: string; scope: string }>();
  @Output() navigate = new EventEmitter<
    'site_selector' | 'reports' | 'validation_reference'
  >();

  private api = inject(ApiService);
  private fb = inject(FormBuilder);

  orgSearchCtrl = this.fb.control('');
  siteSearchCtrl = this.fb.nonNullable.control('');
  cableTestsCtrl = this.fb.control(false);
  configErrorsCtrl = this.fb.control(false);

  currentOrg = signal<{ id: string; name: string; role?: string } | null>(null);
  allSites = signal<Site[]>([]);
  sitesLoading = signal(false);
  selectedSite = signal<Site | null>(null);

  recentReports = signal<ReportRow[]>([]);

  tdrSiteIds = signal<string[]>([]);
  tdrGroupName = signal('');
  tdrGroupExists = signal(true);

  scope = signal<'site' | 'org'>('site');
  budget = signal<BudgetInfo | null>(null);
  budgetLoading = signal(false);
  // Monotonic request id — fetchBudget can fire from multiple inputs (org, site,
  // toggle changes). If an older response arrives after a newer one was issued, it
  // would clobber the current selection. We capture the id at request time and
  // ignore responses whose id no longer matches the latest.
  private budgetReqId = 0;

  generating = signal(false);
  startError = signal('');

  private orgQuery = toSignal(
    this.orgSearchCtrl.valueChanges.pipe(debounceTime(0), distinctUntilChanged()),
    { initialValue: '' },
  );

  filteredOrgs = computed(() => {
    const raw = this.orgQuery() ?? '';
    const q = (typeof raw === 'string' ? raw : '').toLowerCase();
    return this.authInfo()
      .orgs.filter((o) => o.name.toLowerCase().includes(q))
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  });

  private siteQuery = toSignal(
    this.siteSearchCtrl.valueChanges.pipe(debounceTime(0), distinctUntilChanged()),
    { initialValue: '' },
  );

  filteredSites = computed(() => {
    const q = (this.siteQuery() ?? '').toLowerCase().trim();
    const sites = this.allSites();
    if (!q) return sites;
    return sites.filter((s) => s.name.toLowerCase().includes(q));
  });

  // Map of site_id → most recent report status (for the Report column)
  reportStatusBySite = computed<Map<string, string>>(() => {
    const map = new Map<string, string>();
    for (const r of this.recentReports()) {
      if (r.site_id && !map.has(r.site_id)) {
        map.set(r.site_id, r.status);
      }
    }
    return map;
  });

  canGenerate = computed(() => {
    const hasOrg = !!(this.currentOrg() || this.authInfo().orgs.length === 1);
    if (!hasOrg) return false;
    if (this.budgetLoading()) return false;
    if (this.scope() === 'org') {
      return this.budget()?.allowed ?? false;
    }
    if (!this.selectedSite()) return false;
    const b = this.budget();
    return !b || b.allowed;
  });

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
      return `Cable tests are not available. The site group "${name}" does not exist in this organization.`;
    }
    if (!this.siteInTdrGroup()) {
      const name = this.tdrGroupName();
      return `Cable tests are not enabled for this site. Add it to the "${name}" site group in Mist to enable.`;
    }
    return '';
  });

  budgetPct = computed(() => {
    const b = this.budget();
    if (!b) return 0;
    // Already over the API limit (used > limit makes available negative): show full bar.
    if (b.available <= 0) return 100;
    return Math.min(100, Math.max(0, Math.round((b.estimated / b.available) * 100)));
  });

  totalDevices = computed(() => {
    const b = this.budget();
    if (!b) return 0;
    const d = b.device_counts;
    return (d.ap ?? 0) + (d.switch ?? 0) + (d.gateway ?? 0);
  });

  constructor() {
    effect(() => {
      if (!this.cableTestsAllowed()) {
        this.cableTestsCtrl.setValue(false);
        this.cableTestsCtrl.disable({ emitEvent: false });
      } else {
        this.cableTestsCtrl.enable({ emitEvent: false });
      }
    });

    this.configErrorsCtrl.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.fetchBudget());
    this.cableTestsCtrl.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.fetchBudget());
  }

  ngOnInit(): void {
    const initialOrg =
      this.selectedOrg() ?? (this.authInfo().orgs.length === 1 ? this.authInfo().orgs[0] : null);
    if (initialOrg) {
      this.currentOrg.set(initialOrg);
      if (this.authInfo().orgs.length > 1) {
        this.orgSearchCtrl.setValue(initialOrg as never);
      }
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
    const resolvedOrg =
      org ?? this.selectedOrg() ?? (auth.orgs.length === 1 ? auth.orgs[0] : null);
    const orgId = resolvedOrg?.id ?? '';
    this.api.get<SitesResponse>(`sites?org_id=${orgId}`).subscribe({
      next: (res) => {
        this.allSites.set(res.sites);
        this.tdrSiteIds.set(res.tdr_site_ids);
        this.tdrGroupName.set(res.tdr_group_name);
        this.tdrGroupExists.set(res.tdr_group_exists);
        this.sitesLoading.set(false);
      },
      error: () => this.sitesLoading.set(false),
    });
  }

  loadRecentReports(): void {
    this.api.get<ReportsResponse>('reports').subscribe({
      next: (res) => this.recentReports.set(res.reports),
      error: () => this.recentReports.set([]),
    });
  }

  onOrgSelected(org: { id: string; name: string }): void {
    this.currentOrg.set(org);
    // Sync the form control so the topbar autocomplete (which reuses orgSearchCtrl)
    // shows the org name via displayOrg, instead of the search string the user typed
    // when picking from the landing list.
    this.orgSearchCtrl.setValue(org as never);
    this.orgSelected.emit(org);
    this.loadSites(org);
    this.fetchBudget();
  }

  displayOrg(org: { id: string; name: string; role?: string } | null): string {
    return org?.name ?? '';
  }

  selectSite(site: Site): void {
    this.selectedSite.set(site);
    this.fetchBudget();
  }

  onScopeChange(newScope: 'site' | 'org'): void {
    this.scope.set(newScope);
    if (newScope === 'org') {
      this.cableTestsCtrl.setValue(false, { emitEvent: false });
      this.cableTestsCtrl.disable({ emitEvent: false });
    } else {
      if (this.cableTestsAllowed()) this.cableTestsCtrl.enable({ emitEvent: false });
    }
    this.fetchBudget();
  }

  fetchBudget(): void {
    // Bump unconditionally so any in-flight request is marked stale, even if we
    // early-return below (e.g. user clears site selection while a request is pending).
    const reqId = ++this.budgetReqId;
    const auth = this.authInfo();
    const org = this.currentOrg() ?? (auth.orgs.length === 1 ? auth.orgs[0] : null);
    if (!org) return;

    const params = new URLSearchParams();
    params.set('org_id', org.id);
    params.set('include_config_errors', String(!!this.configErrorsCtrl.value));

    if (this.scope() === 'site') {
      const site = this.selectedSite();
      if (!site) {
        this.budget.set(null);
        this.budgetLoading.set(false);
        return;
      }
      params.set('site_id', site.id);
      params.set('include_cable_tests', String(!!this.cableTestsCtrl.value));
    }

    this.budgetLoading.set(true);
    this.api.get<BudgetInfo>(`reports/budget?${params.toString()}`).subscribe({
      next: (budget) => {
        if (reqId !== this.budgetReqId) return; // stale — a newer request superseded this one
        this.budget.set(budget);
        this.budgetLoading.set(false);
        if (!budget.config_errors_allowed) {
          this.configErrorsCtrl.setValue(false, { emitEvent: false });
          this.configErrorsCtrl.disable({ emitEvent: false });
        } else {
          this.configErrorsCtrl.enable({ emitEvent: false });
        }
      },
      error: () => {
        if (reqId !== this.budgetReqId) return;
        this.budgetLoading.set(false);
        this.budget.set(null);
      },
    });
  }

  generateReport(): void {
    const auth = this.authInfo();
    const org = this.currentOrg() ?? (auth.orgs.length === 1 ? auth.orgs[0] : null);
    if (!org) return;

    const isOrg = this.scope() === 'org';
    if (!isOrg && !this.selectedSite()) return;

    this.generating.set(true);
    this.startError.set('');

    const body: Record<string, unknown> = {
      org_id: org.id,
      scope: this.scope(),
      include_config_errors: this.configErrorsCtrl.value,
    };
    if (!isOrg) {
      body['site_id'] = this.selectedSite()!.id;
      body['include_cable_tests'] = this.cableTestsCtrl.value;
    }

    this.api.post<StartReportResponse>('reports', body).subscribe({
      next: (res) => {
        this.generating.set(false);
        this.reportStarted.emit({ id: res.id, scope: this.scope() });
      },
      error: (err) => {
        this.generating.set(false);
        const msg = err?.error?.detail ?? err?.error?.message ?? 'Failed to start report.';
        this.startError.set(msg as string);
      },
    });
  }

  onShellNavigate(route: 'site_selector' | 'reports' | 'validation_reference'): void {
    if (route !== 'site_selector') {
      this.navigate.emit(route);
    }
  }
}
