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
import { MatChipsModule } from '@angular/material/chips';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
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
    MatSelectModule,
    MatButtonModule,
    MatCheckboxModule,
    MatChipsModule,
    MatTableModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    StatusBadgeComponent,
  ],
  styles: [
    `
      .page-container {
        max-width: 1200px;
        margin: 0 auto;
        padding: 24px 16px;
      }
      .page-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 24px;
      }
      .page-title {
        font-size: 22px;
        font-weight: 500;
      }
      .user-info {
        font-size: 14px;
        color: rgba(0, 0, 0, 0.54);
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .cards-row {
        display: grid;
        grid-template-columns: 400px 1fr;
        gap: 24px;
        align-items: start;
      }
      @media (max-width: 860px) {
        .cards-row {
          grid-template-columns: 1fr;
        }
      }
      .full-width {
        width: 100%;
      }
      .cable-warn {
        font-size: 12px;
        color: #e65100;
        margin-top: 4px;
        display: flex;
        align-items: center;
        gap: 4px;
      }
      .section-label {
        font-size: 12px;
        font-weight: 500;
        color: rgba(0, 0, 0, 0.54);
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 8px;
      }
      .generate-btn {
        margin-top: 12px;
      }
      .no-reports {
        text-align: center;
        padding: 32px 0;
        color: rgba(0, 0, 0, 0.38);
      }
      .table-scroll {
        overflow-x: auto;
      }
      mat-card {
        padding: 20px;
      }
      mat-card-title {
        font-size: 16px !important;
        margin-bottom: 16px !important;
      }
      .view-link {
        cursor: pointer;
      }
    `,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <div class="page-title">Network Validation</div>
        <div class="user-info">
          <mat-icon style="font-size:18px;width:18px;height:18px;">person</mat-icon>
          {{ authInfo().user_email }}
        </div>
      </div>

      <div class="cards-row">
        <!-- New Report card -->
        <mat-card>
          <mat-card-title>New Report</mat-card-title>

          <!-- Org picker (only if multi-org) -->
          @if (authInfo().orgs.length > 1) {
            <div class="section-label">Organization</div>
            <mat-form-field class="full-width" appearance="outline">
              <mat-label>Select organization</mat-label>
              <mat-select [formControl]="orgCtrl" (selectionChange)="onOrgSelected($event.value)">
                @for (org of authInfo().orgs; track org.id) {
                  <mat-option [value]="org">{{ org.name }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
          }

          <!-- Site picker -->
          <div class="section-label">Site</div>
          <mat-form-field class="full-width" appearance="outline">
            <mat-label>Select site</mat-label>
            <input
              matInput
              [matAutocomplete]="siteAuto"
              [formControl]="siteSearchCtrl"
              placeholder="Type to filter..."
            />
            <mat-autocomplete
              #siteAuto="matAutocomplete"
              [displayWith]="displaySite"
              (optionSelected)="selectedSite.set($event.option.value)"
            >
              @for (site of filteredSites(); track site.id) {
                <mat-option [value]="site">{{ site.name }}</mat-option>
              }
            </mat-autocomplete>
            @if (sitesLoading()) {
              <mat-spinner matSuffix diameter="18"></mat-spinner>
            }
          </mat-form-field>

          <!-- Cable tests -->
          <mat-checkbox [formControl]="cableTestsCtrl">Include cable tests</mat-checkbox>
          @if (cableTestsCtrl.value) {
            <div class="cable-warn">
              <mat-icon style="font-size:14px;width:14px;height:14px;">warning</mat-icon>
              Cable tests can take several minutes on large deployments.
            </div>
          }

          @if (startError()) {
            <div style="color:#f44336;font-size:13px;margin-top:8px;">{{ startError() }}</div>
          }

          <button
            mat-flat-button
            color="primary"
            class="full-width generate-btn"
            [disabled]="!canGenerate() || generating()"
            (click)="generateReport()"
          >
            @if (generating()) {
              <mat-spinner diameter="18" style="display:inline-block;margin-right:8px;"></mat-spinner>
            }
            Generate Report
          </button>
        </mat-card>

        <!-- Recent Reports card -->
        <mat-card>
          <mat-card-title>
            <div style="display:flex;align-items:center;justify-content:space-between;">
              <span>Recent Reports (last 24h)</span>
              <button mat-icon-button (click)="loadRecentReports()" matTooltip="Refresh">
                <mat-icon>refresh</mat-icon>
              </button>
            </div>
          </mat-card-title>

          @if (reportsLoading()) {
            <div style="display:flex;justify-content:center;padding:32px 0;">
              <mat-spinner diameter="32"></mat-spinner>
            </div>
          } @else if (recentReports().length === 0) {
            <div class="no-reports">
              <mat-icon style="font-size:40px;width:40px;height:40px;margin-bottom:8px;">
                assignment
              </mat-icon>
              <div>No reports in the last 24 hours.</div>
            </div>
          } @else {
            <div class="table-scroll">
              <table mat-table [dataSource]="recentReports()" style="width:100%;">
                <ng-container matColumnDef="site_name">
                  <th mat-header-cell *matHeaderCellDef>Site</th>
                  <td mat-cell *matCellDef="let row">{{ row.site_name }}</td>
                </ng-container>

                <ng-container matColumnDef="status">
                  <th mat-header-cell *matHeaderCellDef>Status</th>
                  <td mat-cell *matCellDef="let row">
                    <app-status-badge [status]="row.status" />
                  </td>
                </ng-container>

                <ng-container matColumnDef="created_at">
                  <th mat-header-cell *matHeaderCellDef>Created</th>
                  <td mat-cell *matCellDef="let row">
                    {{ row.created_at | date: 'MMM d, HH:mm' }}
                  </td>
                </ng-container>

                <ng-container matColumnDef="action">
                  <th mat-header-cell *matHeaderCellDef></th>
                  <td mat-cell *matCellDef="let row">
                    <button
                      mat-button
                      color="primary"
                      class="view-link"
                      (click)="viewReport(row.id)"
                    >
                      View
                    </button>
                  </td>
                </ng-container>

                <tr mat-header-row *matHeaderRowDef="reportColumns"></tr>
                <tr mat-row *matRowDef="let row; columns: reportColumns;"></tr>
              </table>
            </div>
          }
        </mat-card>
      </div>
    </div>
  `,
})
export class SiteSelectorComponent implements OnInit {
  authInfo = input.required<AuthInfo>();
  selectedOrg = input<{ id: string; name: string } | null>(null);

  @Output() orgSelected = new EventEmitter<{ id: string; name: string }>();
  @Output() reportStarted = new EventEmitter<string>();

  private api = inject(ApiService);
  private fb = inject(FormBuilder);

  orgCtrl = this.fb.control<{ id: string; name: string } | null>(null);
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
    // Deselect site if the typed text no longer matches the current selection
    this.siteSearchCtrl.valueChanges
      .pipe(debounceTime(0), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe(() => {
        const current = this.selectedSite();
        const text = this.siteSearchCtrl.value ?? '';
        if (current && current.name !== text) {
          this.selectedSite.set(null);
        }
      });
  }

  ngOnInit(): void {
    // Seed local org from parent input (returning to selector) or single-org
    const initialOrg = this.selectedOrg() ?? (this.authInfo().orgs.length === 1 ? this.authInfo().orgs[0] : null);
    if (initialOrg) {
      this.currentOrg.set(initialOrg);
      this.loadSites(initialOrg);
    }
    this.loadRecentReports();
  }

  private authHeaders(org?: { id: string; name: string } | null): Record<string, string> {
    const auth = this.authInfo();
    const resolvedOrg = org ?? this.selectedOrg() ?? (auth.orgs.length === 1 ? auth.orgs[0] : null);
    return {
      ...this.api.mistAuthHeaders(auth),
      ...(resolvedOrg ? { 'X-Mist-Org-Id': resolvedOrg.id } : {}),
      'X-Mist-User-Id': auth.user_id,
    };
  }

  loadSites(org?: { id: string; name: string }): void {
    this.sitesLoading.set(true);
    this.allSites.set([]);
    this.selectedSite.set(null);
    this.siteSearchCtrl.setValue('');

    this.api.get<SitesResponse>('sites', this.authHeaders(org)).subscribe({
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
      .get<ReportsResponse>('reports', { 'X-Mist-User-Id': this.authInfo().user_id })
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

    const headers = {
      ...this.api.mistAuthHeaders(auth),
      'X-Mist-User-Id': auth.user_id,
    };

    this.api
      .post<StartReportResponse>(
        'reports',
        { site_id: site.id, org_id: org.id, include_cable_tests: this.cableTestsCtrl.value },
        headers,
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

  viewReport(id: string): void {
    this.reportStarted.emit(id);
  }
}
