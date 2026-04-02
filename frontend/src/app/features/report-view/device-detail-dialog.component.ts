import { Component, computed, inject, signal } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatTableModule } from '@angular/material/table';

import { MatIconModule } from '@angular/material/icon';
import { MatDividerModule } from '@angular/material/divider';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';

export interface DeviceCheck {
  check: string;
  status: string;
  value: string;
}

export interface DeviceEvent {
  category: string;
  display: string;
  sub_id: string | null;
  status: 'triggered' | 'cleared';
  trigger_count: number;
  clear_count: number;
  last_change: number;
}

export interface DeviceResult {
  device_id: string;
  name: string;
  mac: string;
  model: string;
  checks: DeviceCheck[];
  events?: DeviceEvent[];
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

export interface PortMember {
  interface: string;
  up: boolean;
  neighbor_system_name: string;
  neighbor_port_desc: string;
}

export interface WanPort {
  interface: string;
  name: string;
  up: boolean;
  wan_type: string;
  lldp: string;
  members?: PortMember[];
}

export interface LanPort {
  interface: string;
  network: string;
  up: boolean;
  lldp: string;
  members?: PortMember[];
}

export interface NetworkInfo {
  name: string;
  gateway_ip: string;
  dhcp_status: string;
  dhcp_pool: string;
  dhcp_relay_servers: string[];
}

export interface DialogData {
  device: DeviceResult | SwitchResult | GatewayResult;
  type: 'ap' | 'switch' | 'gateway';
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
    MatIconModule,
    MatDividerModule,
    MatSlideToggleModule,
    StatusBadgeComponent,
  ],
  templateUrl: './device-detail-dialog.component.html',
  styleUrl: './device-detail-dialog.component.scss',
})
export class DeviceDetailDialogComponent {
  data = inject<DialogData>(MAT_DIALOG_DATA);
  private dialogRef = inject(MatDialogRef<DeviceDetailDialogComponent>);

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
  memberColumns = ['interface', 'up', 'lldp'];
  networkColumns = ['network', 'gateway_ip', 'dhcp_status', 'dhcp_detail'];
  eventColumns = ['display', 'sub_id', 'status', 'trigger_count', 'last_change'];

  isSwitchResult = isSwitchResult;

  showAllEvents = signal(false);

  filteredEvents = computed(() => {
    const events = this.data.device.events ?? [];
    if (this.showAllEvents()) return events;
    return events.filter((e) => e.status === 'triggered');
  });

  formatEventTime(epoch: number): string {
    if (!epoch) return '';
    return new Date(epoch * 1000).toLocaleString();
  }

  close(): void {
    this.dialogRef.close();
  }
}
