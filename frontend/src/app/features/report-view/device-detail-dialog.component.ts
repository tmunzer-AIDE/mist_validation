import { Component, computed, inject, signal } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatTableModule } from '@angular/material/table';

import { MatIconModule } from '@angular/material/icon';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';
import { MvToggleComponent } from '../../shared/components/mv-toggle/mv-toggle.component';
import { deviceTypeIcon, worstStatus } from '../../shared/utils/report-helpers';

export interface DeviceCheck {
  check: string;
  status: string;
  value: string;
  expected?: string;
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

export interface VirtualChassis {
  status: string;
  members: VcMember[];
}

export interface LldpNeighbor {
  port_id: string;
  neighbor_system_name: string;
  neighbor_port_desc: string;
}

export interface PortOptics {
  port_id: string;
  media_type: string;
  xcvr_model: string;
  xcvr_serial: string;
  xcvr_part_number: string;
  rx_power: number | null;
  tx_power: number | null;
  rx_power_status: string;
  tx_power_status: string;
  temperature: number | null;
  bias_current: number | null;
  voltage: number | null;
}

export interface SwitchResult extends DeviceResult {
  virtual_chassis: VirtualChassis | null;
  cable_tests: CableTestResult[];
  lldp_neighbors: LldpNeighbor[];
  port_optics: PortOptics[];
  config_errors: string[];
}

export interface ClusterMember {
  node_name: string;
  mac: string;
  model: string;
  firmware: string;
  status: string;
  ha_state: string;
}

export interface RethInterface {
  name: string;
  status: string;
}

export interface ClusterConfig {
  configuration: string;
  operational: string;
  primary_node_health: string;
  secondary_node_health: string;
  control_link: { name?: string; status?: string };
  fabric_link: { Status?: string; State?: string };
  reth_interfaces: RethInterface[];
}

export interface GatewayCluster {
  status: string;
  members: ClusterMember[];
  config?: ClusterConfig;
}

export interface GatewayResult extends DeviceResult {
  cluster: GatewayCluster | null;
  wan_ports: WanPort[];
  lan_ports: LanPort[];
  networks: NetworkInfo[];
  port_optics: PortOptics[];
  config_errors: string[];
}

export interface VcMember {
  member_id: number;
  model: string;
  firmware: string;
  vc_ports_up: number;
  status: string;
}

export interface CablePair {
  pair: string;
  status: string;
  length: string;
}

export interface CableTestResult {
  port: string;
  status: string;
  pairs: CablePair[];
  neighbor_system_name?: string;
  neighbor_port_desc?: string;
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

/** Natural sort for port IDs like ge-0/0/1, ge-0/0/11, ge-1/0/0 */
function comparePortIds(a: string, b: string): number {
  const partsA = a.split(/(\d+)/);
  const partsB = b.split(/(\d+)/);
  for (let i = 0; i < Math.min(partsA.length, partsB.length); i++) {
    const pa = partsA[i], pb = partsB[i];
    const na = Number(pa), nb = Number(pb);
    if (!isNaN(na) && !isNaN(nb)) {
      if (na !== nb) return na - nb;
    } else {
      const cmp = pa.localeCompare(pb);
      if (cmp !== 0) return cmp;
    }
  }
  return partsA.length - partsB.length;
}

@Component({
  selector: 'app-device-detail-dialog',
  standalone: true,
  imports: [
    MatDialogModule,
    MatButtonModule,
    MatTableModule,
    MatIconModule,
    MvToggleComponent,
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

  checksColumns = ['check', 'status', 'value', 'expected'];

  get checkSummary(): { pass: number; fail: number; warn: number; total: number } {
    const checks = this.data.device.checks ?? [];
    return {
      pass: checks.filter((c) => c.status === 'pass').length,
      fail: checks.filter((c) => c.status === 'fail').length,
      warn: checks.filter((c) => c.status === 'warn').length,
      total: checks.length,
    };
  }

  vcColumns = ['member_id', 'model', 'firmware', 'vc_ports_up', 'status'];
  cableColumns = ['port', 'lldp_neighbor', 'status', 'pairs'];
  lldpColumns = ['port_id', 'neighbor'];
  clusterColumns = ['node_name', 'model', 'firmware', 'status', 'ha_state'];
  rethColumns = ['name', 'status'];
  wanColumns = ['interface', 'name', 'up', 'wan_type', 'lldp'];
  lanColumns = ['interface', 'network', 'up', 'lldp'];
  memberColumns = ['interface', 'up', 'lldp'];
  networkColumns = ['network', 'gateway_ip', 'dhcp_status', 'dhcp_detail'];
  opticsColumns = ['port_id', 'xcvr_model', 'xcvr_serial', 'xcvr_part_number', 'rx_power', 'tx_power', 'temperature'];
  eventColumns = ['display', 'sub_id', 'status', 'trigger_count', 'last_change'];

  isSwitchResult = isSwitchResult;

  // Sorted data sources for switch tables
  get sortedVcMembers(): VcMember[] {
    return [...(this.switchData.virtual_chassis?.members ?? [])].sort((a, b) => a.member_id - b.member_id);
  }

  get sortedCableTests(): CableTestResult[] {
    return [...(this.switchData.cable_tests ?? [])].sort((a, b) => comparePortIds(a.port, b.port));
  }

  get sortedLldpNeighbors(): LldpNeighbor[] {
    return [...(this.switchData.lldp_neighbors ?? [])].sort((a, b) =>
      comparePortIds(a.port_id, b.port_id),
    );
  }

  // Sorted data sources for gateway tables
  get sortedClusterMembers(): ClusterMember[] {
    return [...(this.gatewayData.cluster?.members ?? [])].sort((a, b) => a.node_name.localeCompare(b.node_name));
  }

  get sortedRethInterfaces(): RethInterface[] {
    return [...(this.gatewayData.cluster?.config?.reth_interfaces ?? [])].sort((a, b) => comparePortIds(a.name, b.name));
  }

  get sortedWanPorts(): WanPort[] {
    return [...(this.gatewayData.wan_ports ?? [])].sort((a, b) =>
      comparePortIds(a.interface, b.interface),
    );
  }

  get sortedLanPorts(): LanPort[] {
    return [...(this.gatewayData.lan_ports ?? [])].sort((a, b) =>
      comparePortIds(a.interface, b.interface),
    );
  }

  get sortedNetworks(): NetworkInfo[] {
    return [...(this.gatewayData.networks ?? [])].sort((a, b) =>
      a.name.localeCompare(b.name),
    );
  }

  get sortedPortOptics(): PortOptics[] {
    const optics = (this.data.device as SwitchResult | GatewayResult).port_optics ?? [];
    return [...optics].sort((a, b) => comparePortIds(a.port_id, b.port_id));
  }

  opticsStatusClass(status: string): string {
    if (status === 'pass') return 'status-pass';
    if (status === 'warn') return 'status-warn';
    if (status === 'fail') return 'status-fail';
    return 'status-info';
  }

  showAllEvents = signal(false);

  filteredEvents = computed(() => {
    const events = this.data.device.events ?? [];
    const filtered = this.showAllEvents() ? events : events.filter((e) => e.status === 'triggered');
    return [...filtered].sort((a, b) => a.display.localeCompare(b.display));
  });

  formatLldp(r: CableTestResult): string {
    const parts = [r.neighbor_system_name, r.neighbor_port_desc ? `(${r.neighbor_port_desc})` : ''].filter(Boolean);
    return parts.join(' ') || '—';
  }

  getCheckValue(id: string): string {
    return this.data.device.checks.find((c) => c.check === id)?.value ?? '';
  }

  getCheckStatus(id: string): string {
    return this.data.device.checks.find((c) => c.check === id)?.status ?? 'info';
  }

  getCheckExpected(id: string): string {
    return this.data.device.checks.find((c) => c.check === id)?.expected ?? '';
  }

  get overallStatus(): string {
    return worstStatus(this.data.device.checks ?? []);
  }

  get deviceIcon(): string {
    return deviceTypeIcon(this.data.type);
  }

  copyDeviceId(): void {
    void navigator.clipboard?.writeText(this.data.device.device_id);
  }

  pairClass(status: string): string {
    const s = status.toLowerCase();
    if (s === 'normal') return 'pair-normal';
    return 'pair-fault';
  }

  formatEventTime(epoch: number): string {
    if (!epoch) return '';
    return new Date(epoch * 1000).toLocaleString();
  }

  close(): void {
    this.dialogRef.close();
  }
}
