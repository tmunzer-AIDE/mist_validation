import {
  Component,
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
import { MatChipsModule } from '@angular/material/chips';
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
import { AuthInfo } from '../../app.component';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';
import {
  DeviceDetailDialogComponent,
  SwitchResult,
  GatewayResult,
  DeviceCheck,
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
  mist_user_id: string;
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

function deviceOverallStatus(checks: DeviceCheck[]): string {
  if (checks.some((c) => c.status === 'fail')) return 'fail';
  if (checks.some((c) => c.status === 'warn')) return 'warn';
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
    MatChipsModule,
    MatDividerModule,
    MatExpansionModule,
    MatIconModule,
    MatProgressBarModule,
    MatProgressSpinnerModule,
    MatTableModule,
    MatTooltipModule,
    StatusBadgeComponent,
  ],
  styles: [
    `
      .page-container {
        max-width: 1400px;
        margin: 0 auto;
        padding: 24px 16px;
      }
      .topbar {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 24px;
      }
      .topbar-title {
        font-size: 20px;
        font-weight: 500;
        flex: 1;
      }
      .topbar-meta {
        font-size: 13px;
        color: rgba(0, 0, 0, 0.54);
      }
      .section {
        margin-bottom: 24px;
      }
      .section-title {
        font-size: 15px;
        font-weight: 500;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .info-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
        gap: 16px;
      }
      .info-cell label {
        font-size: 11px;
        font-weight: 500;
        color: rgba(0, 0, 0, 0.54);
        text-transform: uppercase;
        letter-spacing: 0.5px;
        display: block;
        margin-bottom: 4px;
      }
      .info-cell .value {
        font-size: 14px;
      }
      .summary-cards {
        display: flex;
        gap: 16px;
        flex-wrap: wrap;
        margin-bottom: 24px;
      }
      .summary-card {
        flex: 1;
        min-width: 120px;
        text-align: center;
        padding: 16px;
        border-radius: 8px;
      }
      .summary-card .count {
        font-size: 32px;
        font-weight: 300;
        line-height: 1;
      }
      .summary-card .label {
        font-size: 12px;
        color: rgba(0, 0, 0, 0.54);
        margin-top: 4px;
      }
      .device-cards {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 16px;
        margin-bottom: 24px;
      }
      @media (max-width: 700px) {
        .device-cards {
          grid-template-columns: 1fr;
        }
      }
      .device-stat-card {
        padding: 20px;
        border-left: 4px solid #1976d2;
        border-radius: 4px;
      }
      .device-stat-card.has-failures {
        border-left-color: #f44336;
      }
      .device-stat-card .dtype {
        font-size: 12px;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: rgba(0, 0, 0, 0.54);
      }
      .device-stat-card .total {
        font-size: 28px;
        font-weight: 300;
      }
      .device-stat-card .failed {
        font-size: 13px;
        color: #f44336;
      }
      .chip-list {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-bottom: 12px;
      }
      .result-chip {
        font-size: 13px;
        background: #e3f2fd;
        color: #1565c0;
        padding: 2px 10px;
        border-radius: 12px;
        display: inline-block;
      }
      .chip-label {
        font-size: 12px;
        font-weight: 500;
        color: rgba(0, 0, 0, 0.54);
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 6px;
      }
      .progress-steps {
        display: flex;
        flex-direction: column;
        gap: 8px;
        margin-top: 12px;
      }
      .step-row {
        display: flex;
        align-items: flex-start;
        gap: 10px;
      }
      .step-icon {
        margin-top: 2px;
      }
      .step-icon.done {
        color: #4caf50;
      }
      .step-icon.running {
        color: #1976d2;
      }
      .step-icon.pending {
        color: rgba(0, 0, 0, 0.38);
      }
      .step-icon.failed {
        color: #f44336;
      }
      .step-label {
        font-size: 14px;
        font-weight: 500;
      }
      .step-msg {
        font-size: 12px;
        color: rgba(0, 0, 0, 0.54);
      }
      .progress-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;
      }
      .export-row {
        display: flex;
        gap: 12px;
        margin-bottom: 24px;
      }
      .table-wrap {
        overflow-x: auto;
        border-radius: 8px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
        margin-bottom: 24px;
      }
      table {
        width: 100%;
      }
      tr.clickable-row {
        cursor: pointer;
      }
      tr.clickable-row:hover {
        background: rgba(25, 118, 210, 0.04);
      }
      .failed-view {
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 48px 0;
        text-align: center;
      }
      .failed-icon {
        font-size: 48px;
        width: 48px;
        height: 48px;
        color: #f44336;
        margin-bottom: 16px;
      }
      mat-card {
        padding: 20px;
        margin-bottom: 0;
      }
    `,
  ],
  template: `
    <div class="page-container">
      <!-- Topbar -->
      <div class="topbar">
        <button mat-icon-button (click)="back.emit()" matTooltip="Back to site selector">
          <mat-icon>arrow_back</mat-icon>
        </button>
        <div class="topbar-title">
          {{ report()?.site_name ?? 'Validation Report' }}
        </div>
        @if (report()) {
          <div class="topbar-meta">
            {{ report()!.created_at | date: 'MMM d, y HH:mm' }}
          </div>
          <app-status-badge [status]="report()!.status" />
        }
      </div>

      @if (!report()) {
        <div style="display:flex;justify-content:center;padding:80px 0;">
          <mat-spinner></mat-spinner>
        </div>
      }

      <!-- Progress view: pending or running -->
      @if (report() && (report()!.status === 'pending' || report()!.status === 'running')) {
        <mat-card class="section">
          <div class="progress-header">
            <div style="font-size:15px;font-weight:500;">Running validation...</div>
            @if (report()!.progress.overall_total > 0) {
              <div style="font-size:13px;color:rgba(0,0,0,.54);">
                {{ report()!.progress.overall_completed }} / {{ report()!.progress.overall_total }}
              </div>
            }
          </div>

          <mat-progress-bar
            [mode]="report()!.progress.overall_total === 0 ? 'indeterminate' : 'determinate'"
            [value]="progressPercent()"
          ></mat-progress-bar>

          <div class="progress-steps">
            @for (step of report()!.progress.steps; track step.id) {
              <div class="step-row">
                <mat-icon class="step-icon" [class]="stepIconClass(step.status)">
                  {{ stepIcon(step.status) }}
                </mat-icon>
                <div>
                  <div class="step-label">{{ step.label }}</div>
                  @if (step.message) {
                    <div class="step-msg">{{ step.message }}</div>
                  }
                </div>
              </div>
            }
          </div>
        </mat-card>
      }

      <!-- Failed view -->
      @if (report() && report()!.status === 'failed') {
        <mat-card>
          <div class="failed-view">
            <mat-icon class="failed-icon">error_outline</mat-icon>
            <div style="font-size:18px;font-weight:500;margin-bottom:8px;">Report Failed</div>
            <div style="color:rgba(0,0,0,.54);max-width:480px;">
              {{ report()?.error ?? 'An unexpected error occurred during validation.' }}
            </div>
            <button mat-stroked-button style="margin-top:24px;" (click)="back.emit()">
              Back to Site Selector
            </button>
          </div>
        </mat-card>
      }

      <!-- Completed view -->
      @if (report() && report()!.status === 'completed' && report()!.result) {
        <!-- Export buttons -->
        <div class="export-row">
          <button mat-stroked-button (click)="exportPdf()">
            <mat-icon>picture_as_pdf</mat-icon>
            Export PDF
          </button>
          <button mat-stroked-button (click)="exportCsv()">
            <mat-icon>download</mat-icon>
            Export CSV
          </button>
        </div>

        <!-- Summary chips -->
        <div class="summary-cards">
          <mat-card class="summary-card" style="border-top:3px solid #4caf50;">
            <div class="count status-pass">{{ report()!.result!.summary.pass }}</div>
            <div class="label">Passed</div>
          </mat-card>
          <mat-card class="summary-card" style="border-top:3px solid #f44336;">
            <div class="count status-fail">{{ report()!.result!.summary.fail }}</div>
            <div class="label">Failed</div>
          </mat-card>
          <mat-card class="summary-card" style="border-top:3px solid #ff9800;">
            <div class="count status-warn">{{ report()!.result!.summary.warn }}</div>
            <div class="label">Warnings</div>
          </mat-card>
          <mat-card class="summary-card" style="border-top:3px solid #2196f3;">
            <div class="count status-info">{{ report()!.result!.summary.info }}</div>
            <div class="label">Info</div>
          </mat-card>
        </div>

        <!-- Site info -->
        <mat-card class="section">
          <div class="section-title">
            <mat-icon>location_on</mat-icon> Site Information
          </div>
          <div class="info-grid" style="margin-bottom:16px;">
            <div class="info-cell">
              <label>Site Name</label>
              <div class="value">{{ report()!.result!.site_info.site_name }}</div>
            </div>
            <div class="info-cell">
              <label>Address</label>
              <div class="value">{{ report()!.result!.site_info.site_address || '—' }}</div>
            </div>
          </div>

          @if (report()!.result!.site_info.site_groups.length) {
            <div class="chip-label">Site Groups</div>
            <div class="chip-list">
              @for (g of report()!.result!.site_info.site_groups; track g) {
                <span class="result-chip">{{ g }}</span>
              }
            </div>
          }

          @if (report()!.result!.site_info.templates.length) {
            <div class="chip-label">Templates</div>
            <div class="chip-list">
              @for (t of report()!.result!.site_info.templates; track t.name) {
                <span class="result-chip">{{ t.type }}: {{ t.name }}</span>
              }
            </div>
          }

          @if (
            report()!.result!.site_info.org_wlans.length ||
            report()!.result!.site_info.site_wlans.length
          ) {
            <div class="chip-label">WLANs</div>
            <div class="chip-list">
              @for (w of report()!.result!.site_info.org_wlans; track w.ssid) {
                <span class="result-chip" style="background:#f3e5f5;color:#6a1b9a;">
                  org: {{ w.ssid }}
                </span>
              }
              @for (w of report()!.result!.site_info.site_wlans; track w.ssid) {
                <span class="result-chip" style="background:#e8f5e9;color:#2e7d32;">
                  site: {{ w.ssid }}
                </span>
              }
            </div>
          }
        </mat-card>

        <!-- Device summary -->
        <div class="device-cards">
          @for (dtype of deviceSummaryEntries(); track dtype.key) {
            <mat-card
              class="device-stat-card"
              [class.has-failures]="dtype.value.failed > 0"
            >
              <div class="dtype">{{ dtype.key | titlecase }}</div>
              <div class="total">{{ dtype.value.total }}</div>
              <div class="failed">
                @if (dtype.value.failed > 0) {
                  {{ dtype.value.failed }} failed
                } @else {
                  All healthy
                }
              </div>
            </mat-card>
          }
        </div>

        <!-- Template variables -->
        @if (report()!.result!.template_variables.length) {
          <mat-expansion-panel class="section" style="margin-bottom:24px;">
            <mat-expansion-panel-header>
              <mat-panel-title>
                Template Variables ({{ report()!.result!.template_variables.length }})
              </mat-panel-title>
            </mat-expansion-panel-header>
            <div class="table-wrap" style="margin-top:12px;">
              <table mat-table [dataSource]="report()!.result!.template_variables">
                <ng-container matColumnDef="status">
                  <th mat-header-cell *matHeaderCellDef>Status</th>
                  <td mat-cell *matCellDef="let r">
                    <app-status-badge [status]="r.status" />
                  </td>
                </ng-container>
                <ng-container matColumnDef="template">
                  <th mat-header-cell *matHeaderCellDef>Template</th>
                  <td mat-cell *matCellDef="let r">{{ r.template }}</td>
                </ng-container>
                <ng-container matColumnDef="variable">
                  <th mat-header-cell *matHeaderCellDef>Variable</th>
                  <td mat-cell *matCellDef="let r">{{ r.variable }}</td>
                </ng-container>
                <ng-container matColumnDef="value">
                  <th mat-header-cell *matHeaderCellDef>Value</th>
                  <td mat-cell *matCellDef="let r">{{ r.value || '—' }}</td>
                </ng-container>
                <tr mat-header-row *matHeaderRowDef="tvColumns"></tr>
                <tr mat-row *matRowDef="let row; columns: tvColumns;"></tr>
              </table>
            </div>
          </mat-expansion-panel>
        }

        <!-- APs table -->
        @if (report()!.result!.aps.length) {
          <div class="section">
            <div class="section-title">
              <mat-icon>wifi</mat-icon>
              Access Points ({{ report()!.result!.aps.length }})
            </div>
            <div class="table-wrap">
              <table mat-table [dataSource]="report()!.result!.aps">
                <ng-container matColumnDef="status">
                  <th mat-header-cell *matHeaderCellDef>Status</th>
                  <td mat-cell *matCellDef="let r">
                    <app-status-badge [status]="deviceOverallStatus(r.checks)" />
                  </td>
                </ng-container>
                <ng-container matColumnDef="name">
                  <th mat-header-cell *matHeaderCellDef>Name</th>
                  <td mat-cell *matCellDef="let r">{{ r.name }}</td>
                </ng-container>
                <ng-container matColumnDef="model">
                  <th mat-header-cell *matHeaderCellDef>Model</th>
                  <td mat-cell *matCellDef="let r">{{ r.model }}</td>
                </ng-container>
                <ng-container matColumnDef="connection">
                  <th mat-header-cell *matHeaderCellDef>Connection</th>
                  <td mat-cell *matCellDef="let r">
                    <app-status-badge
                      [status]="getCheckStatus(r.checks, 'connection')"
                      [label]="getCheckValue(r.checks, 'connection')"
                    />
                  </td>
                </ng-container>
                <ng-container matColumnDef="firmware">
                  <th mat-header-cell *matHeaderCellDef>Firmware</th>
                  <td mat-cell *matCellDef="let r">
                    <span [class]="'status-' + getCheckStatus(r.checks, 'firmware')">
                      {{ getCheckValue(r.checks, 'firmware') || '—' }}
                    </span>
                  </td>
                </ng-container>
                <ng-container matColumnDef="eth0_speed">
                  <th mat-header-cell *matHeaderCellDef>Eth0 Speed</th>
                  <td mat-cell *matCellDef="let r">
                    <span [class]="'status-' + getCheckStatus(r.checks, 'eth0_speed')">
                      {{ getCheckValue(r.checks, 'eth0_speed') || '—' }}
                    </span>
                  </td>
                </ng-container>
                <ng-container matColumnDef="power">
                  <th mat-header-cell *matHeaderCellDef>Power</th>
                  <td mat-cell *matCellDef="let r">
                    <span [class]="'status-' + getCheckStatus(r.checks, 'power')">
                      {{ getCheckValue(r.checks, 'power') || '—' }}
                    </span>
                  </td>
                </ng-container>
                <ng-container matColumnDef="config">
                  <th mat-header-cell *matHeaderCellDef>Config</th>
                  <td mat-cell *matCellDef="let r">
                    <app-status-badge
                      [status]="getCheckStatus(r.checks, 'config')"
                      [label]="getCheckValue(r.checks, 'config')"
                    />
                  </td>
                </ng-container>
                <ng-container matColumnDef="lldp">
                  <th mat-header-cell *matHeaderCellDef>LLDP</th>
                  <td mat-cell *matCellDef="let r">
                    <span [class]="'status-' + getCheckStatus(r.checks, 'lldp')">
                      {{ getCheckValue(r.checks, 'lldp') || '—' }}
                    </span>
                  </td>
                </ng-container>
                <tr mat-header-row *matHeaderRowDef="apColumns"></tr>
                <tr mat-row *matRowDef="let row; columns: apColumns;"></tr>
              </table>
            </div>
          </div>
        }

        <!-- Switches table -->
        @if (report()!.result!.switches.length) {
          <div class="section">
            <div class="section-title">
              <mat-icon>device_hub</mat-icon>
              Switches ({{ report()!.result!.switches.length }})
            </div>
            <div class="table-wrap">
              <table mat-table [dataSource]="report()!.result!.switches">
                <ng-container matColumnDef="status">
                  <th mat-header-cell *matHeaderCellDef>Status</th>
                  <td mat-cell *matCellDef="let r">
                    <app-status-badge [status]="deviceOverallStatus(r.checks)" />
                  </td>
                </ng-container>
                <ng-container matColumnDef="name">
                  <th mat-header-cell *matHeaderCellDef>Name</th>
                  <td mat-cell *matCellDef="let r">{{ r.name }}</td>
                </ng-container>
                <ng-container matColumnDef="model">
                  <th mat-header-cell *matHeaderCellDef>Model</th>
                  <td mat-cell *matCellDef="let r">{{ r.model }}</td>
                </ng-container>
                <ng-container matColumnDef="connection">
                  <th mat-header-cell *matHeaderCellDef>Connection</th>
                  <td mat-cell *matCellDef="let r">
                    <app-status-badge
                      [status]="getCheckStatus(r.checks, 'connection')"
                      [label]="getCheckValue(r.checks, 'connection')"
                    />
                  </td>
                </ng-container>
                <ng-container matColumnDef="firmware">
                  <th mat-header-cell *matHeaderCellDef>Firmware</th>
                  <td mat-cell *matCellDef="let r">
                    <span [class]="'status-' + getCheckStatus(r.checks, 'firmware')">
                      {{ getCheckValue(r.checks, 'firmware') || '—' }}
                    </span>
                  </td>
                </ng-container>
                <ng-container matColumnDef="config">
                  <th mat-header-cell *matHeaderCellDef>Config</th>
                  <td mat-cell *matCellDef="let r">
                    <app-status-badge
                      [status]="getCheckStatus(r.checks, 'config')"
                      [label]="getCheckValue(r.checks, 'config')"
                    />
                  </td>
                </ng-container>
                <ng-container matColumnDef="cable_tests">
                  <th mat-header-cell *matHeaderCellDef>Cable Tests</th>
                  <td mat-cell *matCellDef="let r">
                    @if (r.cable_tests?.length) {
                      <span [class]="'status-' + cableTestStatus(r.cable_tests)">
                        {{ r.cable_tests.length }} tests
                      </span>
                    } @else {
                      <span style="color:rgba(0,0,0,.38);">—</span>
                    }
                  </td>
                </ng-container>
                <tr mat-header-row *matHeaderRowDef="switchColumns"></tr>
                <tr
                  mat-row
                  *matRowDef="let row; columns: switchColumns;"
                  class="clickable-row"
                  (click)="openDeviceDetail(row, 'switch')"
                ></tr>
              </table>
            </div>
          </div>
        }

        <!-- Gateways table -->
        @if (report()!.result!.gateways.length) {
          <div class="section">
            <div class="section-title">
              <mat-icon>router</mat-icon>
              Gateways ({{ report()!.result!.gateways.length }})
            </div>
            <div class="table-wrap">
              <table mat-table [dataSource]="report()!.result!.gateways">
                <ng-container matColumnDef="status">
                  <th mat-header-cell *matHeaderCellDef>Status</th>
                  <td mat-cell *matCellDef="let r">
                    <app-status-badge [status]="deviceOverallStatus(r.checks)" />
                  </td>
                </ng-container>
                <ng-container matColumnDef="name">
                  <th mat-header-cell *matHeaderCellDef>Name</th>
                  <td mat-cell *matCellDef="let r">{{ r.name }}</td>
                </ng-container>
                <ng-container matColumnDef="model">
                  <th mat-header-cell *matHeaderCellDef>Model</th>
                  <td mat-cell *matCellDef="let r">{{ r.model }}</td>
                </ng-container>
                <ng-container matColumnDef="connection">
                  <th mat-header-cell *matHeaderCellDef>Connection</th>
                  <td mat-cell *matCellDef="let r">
                    <app-status-badge
                      [status]="getCheckStatus(r.checks, 'connection')"
                      [label]="getCheckValue(r.checks, 'connection')"
                    />
                  </td>
                </ng-container>
                <ng-container matColumnDef="firmware">
                  <th mat-header-cell *matHeaderCellDef>Firmware</th>
                  <td mat-cell *matCellDef="let r">
                    <span [class]="'status-' + getCheckStatus(r.checks, 'firmware')">
                      {{ getCheckValue(r.checks, 'firmware') || '—' }}
                    </span>
                  </td>
                </ng-container>
                <ng-container matColumnDef="config">
                  <th mat-header-cell *matHeaderCellDef>Config</th>
                  <td mat-cell *matCellDef="let r">
                    <app-status-badge
                      [status]="getCheckStatus(r.checks, 'config')"
                      [label]="getCheckValue(r.checks, 'config')"
                    />
                  </td>
                </ng-container>
                <ng-container matColumnDef="wan">
                  <th mat-header-cell *matHeaderCellDef>WAN</th>
                  <td mat-cell *matCellDef="let r">
                    <span [class]="'status-' + getCheckStatus(r.checks, 'wan')">
                      {{ getCheckValue(r.checks, 'wan') || '—' }}
                    </span>
                  </td>
                </ng-container>
                <ng-container matColumnDef="lan">
                  <th mat-header-cell *matHeaderCellDef>LAN</th>
                  <td mat-cell *matCellDef="let r">
                    <span [class]="'status-' + getCheckStatus(r.checks, 'lan')">
                      {{ getCheckValue(r.checks, 'lan') || '—' }}
                    </span>
                  </td>
                </ng-container>
                <tr mat-header-row *matHeaderRowDef="gatewayColumns"></tr>
                <tr
                  mat-row
                  *matRowDef="let row; columns: gatewayColumns;"
                  class="clickable-row"
                  (click)="openDeviceDetail(row, 'gateway')"
                ></tr>
              </table>
            </div>
          </div>
        }
      }
    </div>
  `,
})
export class ReportViewComponent implements OnInit, OnDestroy {
  jobId = input.required<string>();
  authInfo = input.required<AuthInfo>();

  @Output() back = new EventEmitter<void>();

  private api = inject(ApiService);
  private ws = inject(WsService);
  private dialog = inject(MatDialog);

  report = signal<ReportResponse | null>(null);
  private wsSubscription: { unsubscribe(): void } | null = null;

  // Table column definitions
  tvColumns = ['status', 'template', 'variable', 'value'];
  apColumns = [
    'status',
    'name',
    'model',
    'connection',
    'firmware',
    'eth0_speed',
    'power',
    'config',
    'lldp',
  ];
  switchColumns = ['status', 'name', 'model', 'connection', 'firmware', 'config', 'cable_tests'];
  gatewayColumns = ['status', 'name', 'model', 'connection', 'firmware', 'config', 'wan', 'lan'];

  // Expose helpers to template
  getCheckValue = getCheckValue;
  getCheckStatus = getCheckStatus;
  deviceOverallStatus = deviceOverallStatus;

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

  cableTestStatus(tests: { status: string }[]): string {
    if (tests.some((t) => t.status === 'fail')) return 'fail';
    if (tests.some((t) => t.status === 'warn')) return 'warn';
    return 'pass';
  }

  ngOnInit(): void {
    this.loadReport();
    this.subscribeWs();
  }

  ngOnDestroy(): void {
    const channel = `report:${this.jobId()}`;
    this.ws.unsubscribe(channel);
    this.wsSubscription?.unsubscribe();
  }

  private authHeaders(): Record<string, string> {
    return { 'X-Mist-User-Id': this.authInfo().user_id };
  }

  loadReport(): void {
    this.api.get<ReportResponse>(`reports/${this.jobId()}`, this.authHeaders()).subscribe({
      next: (r) => this.report.set(r),
    });
  }

  private subscribeWs(): void {
    const channel = `report:${this.jobId()}`;
    this.ws.subscribe(channel);
    this.wsSubscription = this.ws.channel$(channel).subscribe((msg) => {
      const type = msg['type'] as string;

      if (type === 'progress' || type === 'report_progress') {
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

      if (type === 'report_complete' || type === 'complete') {
        // Re-fetch full report including result payload
        this.loadReport();
      }

      if (type === 'report_failed' || type === 'failed') {
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

  openDeviceDetail(device: SwitchResult | GatewayResult, type: 'switch' | 'gateway'): void {
    this.dialog.open(DeviceDetailDialogComponent, {
      data: { device, type },
      width: '720px',
      maxWidth: '95vw',
    });
  }

  private exportFile(path: string, filename: string): void {
    this.api.getBlob(path, this.authHeaders()).subscribe({
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
