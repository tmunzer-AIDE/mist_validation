import { Component, inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatTableModule } from '@angular/material/table';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatDividerModule } from '@angular/material/divider';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';

export interface DeviceCheck {
  check: string;
  status: string;
  value: string;
}

export interface DeviceResult {
  device_id: string;
  name: string;
  mac: string;
  model: string;
  checks: DeviceCheck[];
}

export interface SwitchResult extends DeviceResult {
  virtual_chassis: VcMember[] | null;
  cable_tests: CableTestResult[];
}

export interface GatewayResult extends DeviceResult {
  wan_ports: WanPort[];
  lan_ports: LanPort[];
  networks: NetworkInfo[];
}

export interface VcMember {
  member_id: number;
  model: string;
  firmware: string;
  vc_ports_up: number;
  status: string;
}

export interface CableTestResult {
  port: string;
  lldp_neighbor: string;
  status: string;
  pairs: string;
}

export interface WanPort {
  interface: string;
  name: string;
  up: boolean;
  wan_type: string;
  lldp: string;
}

export interface LanPort {
  interface: string;
  network: string;
  up: boolean;
  lldp: string;
}

export interface NetworkInfo {
  network: string;
  gateway_ip: string;
  dhcp_status: string;
  detail: string;
}

export interface DialogData {
  device: SwitchResult | GatewayResult;
  type: 'switch' | 'gateway';
}

function isSwitchResult(d: SwitchResult | GatewayResult): d is SwitchResult {
  return 'cable_tests' in d;
}

@Component({
  selector: 'app-device-detail-dialog',
  standalone: true,
  imports: [
    MatDialogModule,
    MatButtonModule,
    MatTableModule,
    MatChipsModule,
    MatIconModule,
    MatDividerModule,
    StatusBadgeComponent,
  ],
  styles: [
    `
      .dialog-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px 24px 0;
      }
      .device-name {
        font-size: 18px;
        font-weight: 500;
      }
      .device-meta {
        font-size: 13px;
        color: rgba(0, 0, 0, 0.54);
        margin-top: 2px;
      }
      .section-title {
        font-size: 13px;
        font-weight: 500;
        color: rgba(0, 0, 0, 0.54);
        text-transform: uppercase;
        letter-spacing: 0.5px;
        padding: 16px 0 8px;
      }
      .no-data {
        color: rgba(0, 0, 0, 0.38);
        font-size: 13px;
        padding: 12px 0;
        text-align: center;
      }
      table {
        width: 100%;
      }
      .content-area {
        padding: 0 24px 16px;
        min-width: 560px;
        max-height: 70vh;
        overflow-y: auto;
      }
    `,
  ],
  template: `
    <div class="dialog-header">
      <div>
        <div class="device-name">{{ data.device.name }}</div>
        <div class="device-meta">{{ data.device.model }} &bull; {{ data.device.mac }}</div>
      </div>
      <button mat-icon-button (click)="close()">
        <mat-icon>close</mat-icon>
      </button>
    </div>

    <div class="content-area">
      @if (data.type === 'switch') {
        <!-- Virtual Chassis -->
        <div class="section-title">Virtual Chassis Members</div>
        @if (!switchData.virtual_chassis || switchData.virtual_chassis.length === 0) {
          <div class="no-data">No virtual chassis data.</div>
        } @else {
          <table mat-table [dataSource]="switchData.virtual_chassis">
            <ng-container matColumnDef="member_id">
              <th mat-header-cell *matHeaderCellDef>Member</th>
              <td mat-cell *matCellDef="let r">{{ r.member_id }}</td>
            </ng-container>
            <ng-container matColumnDef="model">
              <th mat-header-cell *matHeaderCellDef>Model</th>
              <td mat-cell *matCellDef="let r">{{ r.model }}</td>
            </ng-container>
            <ng-container matColumnDef="firmware">
              <th mat-header-cell *matHeaderCellDef>Firmware</th>
              <td mat-cell *matCellDef="let r">{{ r.firmware }}</td>
            </ng-container>
            <ng-container matColumnDef="vc_ports_up">
              <th mat-header-cell *matHeaderCellDef>VC Ports UP</th>
              <td mat-cell *matCellDef="let r">{{ r.vc_ports_up }}</td>
            </ng-container>
            <ng-container matColumnDef="status">
              <th mat-header-cell *matHeaderCellDef>Status</th>
              <td mat-cell *matCellDef="let r">
                <app-status-badge [status]="r.status" />
              </td>
            </ng-container>
            <tr mat-header-row *matHeaderRowDef="vcColumns"></tr>
            <tr mat-row *matRowDef="let row; columns: vcColumns;"></tr>
          </table>
        }

        <mat-divider></mat-divider>

        <!-- Cable tests -->
        <div class="section-title">Cable Test Results</div>
        @if (switchData.cable_tests.length === 0) {
          <div class="no-data">No cable test results available.</div>
        } @else {
          <table mat-table [dataSource]="switchData.cable_tests">
            <ng-container matColumnDef="port">
              <th mat-header-cell *matHeaderCellDef>Port</th>
              <td mat-cell *matCellDef="let r">{{ r.port }}</td>
            </ng-container>
            <ng-container matColumnDef="lldp_neighbor">
              <th mat-header-cell *matHeaderCellDef>LLDP Neighbor</th>
              <td mat-cell *matCellDef="let r">{{ r.lldp_neighbor || '—' }}</td>
            </ng-container>
            <ng-container matColumnDef="status">
              <th mat-header-cell *matHeaderCellDef>Status</th>
              <td mat-cell *matCellDef="let r">
                <app-status-badge [status]="r.status" />
              </td>
            </ng-container>
            <ng-container matColumnDef="pairs">
              <th mat-header-cell *matHeaderCellDef>Pairs</th>
              <td mat-cell *matCellDef="let r">{{ r.pairs || '—' }}</td>
            </ng-container>
            <tr mat-header-row *matHeaderRowDef="cableColumns"></tr>
            <tr mat-row *matRowDef="let row; columns: cableColumns;"></tr>
          </table>
        }
      }

      @if (data.type === 'gateway') {
        <!-- WAN ports -->
        <div class="section-title">WAN Ports</div>
        @if (gatewayData.wan_ports.length === 0) {
          <div class="no-data">No WAN ports data.</div>
        } @else {
          <table mat-table [dataSource]="gatewayData.wan_ports">
            <ng-container matColumnDef="interface">
              <th mat-header-cell *matHeaderCellDef>Interface</th>
              <td mat-cell *matCellDef="let r">{{ r.interface }}</td>
            </ng-container>
            <ng-container matColumnDef="name">
              <th mat-header-cell *matHeaderCellDef>Name</th>
              <td mat-cell *matCellDef="let r">{{ r.name }}</td>
            </ng-container>
            <ng-container matColumnDef="up">
              <th mat-header-cell *matHeaderCellDef>Status</th>
              <td mat-cell *matCellDef="let r">
                <app-status-badge [status]="r.up ? 'pass' : 'fail'" [label]="r.up ? 'UP' : 'DOWN'" />
              </td>
            </ng-container>
            <ng-container matColumnDef="wan_type">
              <th mat-header-cell *matHeaderCellDef>WAN Type</th>
              <td mat-cell *matCellDef="let r">{{ r.wan_type || '—' }}</td>
            </ng-container>
            <ng-container matColumnDef="lldp">
              <th mat-header-cell *matHeaderCellDef>LLDP</th>
              <td mat-cell *matCellDef="let r">{{ r.lldp || '—' }}</td>
            </ng-container>
            <tr mat-header-row *matHeaderRowDef="wanColumns"></tr>
            <tr mat-row *matRowDef="let row; columns: wanColumns;"></tr>
          </table>
        }

        <mat-divider></mat-divider>

        <!-- LAN ports -->
        <div class="section-title">LAN Ports</div>
        @if (gatewayData.lan_ports.length === 0) {
          <div class="no-data">No LAN ports data.</div>
        } @else {
          <table mat-table [dataSource]="gatewayData.lan_ports">
            <ng-container matColumnDef="interface">
              <th mat-header-cell *matHeaderCellDef>Interface</th>
              <td mat-cell *matCellDef="let r">{{ r.interface }}</td>
            </ng-container>
            <ng-container matColumnDef="network">
              <th mat-header-cell *matHeaderCellDef>Network</th>
              <td mat-cell *matCellDef="let r">{{ r.network || '—' }}</td>
            </ng-container>
            <ng-container matColumnDef="up">
              <th mat-header-cell *matHeaderCellDef>Status</th>
              <td mat-cell *matCellDef="let r">
                <app-status-badge [status]="r.up ? 'pass' : 'fail'" [label]="r.up ? 'UP' : 'DOWN'" />
              </td>
            </ng-container>
            <ng-container matColumnDef="lldp">
              <th mat-header-cell *matHeaderCellDef>LLDP</th>
              <td mat-cell *matCellDef="let r">{{ r.lldp || '—' }}</td>
            </ng-container>
            <tr mat-header-row *matHeaderRowDef="lanColumns"></tr>
            <tr mat-row *matRowDef="let row; columns: lanColumns;"></tr>
          </table>
        }

        <mat-divider></mat-divider>

        <!-- Networks -->
        <div class="section-title">Networks</div>
        @if (gatewayData.networks.length === 0) {
          <div class="no-data">No network data.</div>
        } @else {
          <table mat-table [dataSource]="gatewayData.networks">
            <ng-container matColumnDef="network">
              <th mat-header-cell *matHeaderCellDef>Network</th>
              <td mat-cell *matCellDef="let r">{{ r.network }}</td>
            </ng-container>
            <ng-container matColumnDef="gateway_ip">
              <th mat-header-cell *matHeaderCellDef>Gateway IP</th>
              <td mat-cell *matCellDef="let r">{{ r.gateway_ip || '—' }}</td>
            </ng-container>
            <ng-container matColumnDef="dhcp_status">
              <th mat-header-cell *matHeaderCellDef>DHCP</th>
              <td mat-cell *matCellDef="let r">
                <app-status-badge [status]="r.dhcp_status" />
              </td>
            </ng-container>
            <ng-container matColumnDef="detail">
              <th mat-header-cell *matHeaderCellDef>Detail</th>
              <td mat-cell *matCellDef="let r">{{ r.detail || '—' }}</td>
            </ng-container>
            <tr mat-header-row *matHeaderRowDef="networkColumns"></tr>
            <tr mat-row *matRowDef="let row; columns: networkColumns;"></tr>
          </table>
        }
      }
    </div>

    <mat-dialog-actions align="end">
      <button mat-button (click)="close()">Close</button>
    </mat-dialog-actions>
  `,
})
export class DeviceDetailDialogComponent {
  data = inject<DialogData>(MAT_DIALOG_DATA);
  private dialogRef = inject(MatDialogRef<DeviceDetailDialogComponent>);

  // Typed accessors
  get switchData(): SwitchResult {
    return this.data.device as SwitchResult;
  }

  get gatewayData(): GatewayResult {
    return this.data.device as GatewayResult;
  }

  vcColumns = ['member_id', 'model', 'firmware', 'vc_ports_up', 'status'];
  cableColumns = ['port', 'lldp_neighbor', 'status', 'pairs'];
  wanColumns = ['interface', 'name', 'up', 'wan_type', 'lldp'];
  lanColumns = ['interface', 'network', 'up', 'lldp'];
  networkColumns = ['network', 'gateway_ip', 'dhcp_status', 'detail'];

  isSwitchResult = isSwitchResult;

  close(): void {
    this.dialogRef.close();
  }
}
