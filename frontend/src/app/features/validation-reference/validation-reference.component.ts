import { Component, EventEmitter, Output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';

interface CheckDef {
  name: string;
  id: string;
  description: string;
  pass: string;
  warn: string;
  fail: string;
}

interface CheckSection {
  title: string;
  icon: string;
  description: string;
  checks: CheckDef[];
}

@Component({
  selector: 'app-validation-reference',
  standalone: true,
  imports: [MatButtonModule, MatCardModule, MatIconModule, StatusBadgeComponent],
  templateUrl: './validation-reference.component.html',
  styleUrl: './validation-reference.component.scss',
})
export class ValidationReferenceComponent {
  @Output() back = new EventEmitter<void>();

  sections: CheckSection[] = [
    {
      title: 'Site-Level Checks',
      icon: 'location_on',
      description: 'Checks applied to the site configuration, not tied to individual devices.',
      checks: [
        {
          name: 'Template Variables',
          id: 'template_variables',
          description:
            'Verifies all Jinja2 variables referenced in templates (RF, network, gateway, site) are defined in site settings.',
          pass: 'Variable is defined in site vars',
          warn: '',
          fail: 'Variable is not defined in site vars',
        },
        {
          name: 'Device Events',
          id: 'device_events',
          description:
            'Fetches device events from the last 24 hours and correlates trigger/clear event pairs to identify unresolved alerts.',
          pass: 'Event has been cleared',
          warn: '',
          fail: 'Event is still triggered (not cleared)',
        },
      ],
    },
    {
      title: 'Common Device Checks',
      icon: 'devices',
      description: 'Checks applied to all device types (APs, switches, gateways).',
      checks: [
        {
          name: 'Device Name',
          id: 'name_defined',
          description: 'Verifies the device has a name configured.',
          pass: 'Name is set',
          warn: '',
          fail: 'Name is empty or not set',
        },
        {
          name: 'Firmware Version',
          id: 'firmware_version',
          description:
            'Compares the running firmware against the recommended version. The recommended version is determined by the Mist API, and can be overridden by org-level or site-level auto-upgrade settings.',
          pass: 'Running firmware matches the recommended version',
          warn: 'Running firmware differs from recommended',
          fail: 'AP only: firmware is tagged deprecated or alpha (when not in beta auto-upgrade mode)',
        },
        {
          name: 'Connection Status',
          id: 'connection_status',
          description: 'Checks whether the device is connected to the Mist Cloud.',
          pass: 'Device is connected',
          warn: 'Device is upgrading or restarting',
          fail: 'Device is disconnected or in any other state',
        },
        {
          name: 'Configuration Status',
          id: 'config_status',
          description: 'Checks the latest configuration push event for success or failure.',
          pass: 'Latest config event succeeded',
          warn: '',
          fail: 'Latest config event indicates a failure',
        },
      ],
    },
    {
      title: 'Access Point Checks',
      icon: 'router',
      description: 'Additional checks specific to Mist Access Points.',
      checks: [
        {
          name: 'Eth0 Port Speed',
          id: 'eth0_port_speed',
          description: 'Checks the AP uplink port speed.',
          pass: 'Port speed >= 1 Gbps',
          warn: 'Port speed < 1 Gbps',
          fail: '',
        },
        {
          name: 'Power Constrained',
          id: 'power_constrained',
          description: 'Checks if the AP is power-limited by its PoE source.',
          pass: 'AP is not power constrained',
          warn: 'AP is power constrained',
          fail: '',
        },
        {
          name: 'LLDP Neighbor',
          id: 'lldp_neighbor',
          description:
            'Reports the upstream switch name and port detected via LLDP. Informational only (no pass/fail).',
          pass: '',
          warn: '',
          fail: '',
        },
      ],
    },
    {
      title: 'Switch Checks',
      icon: 'lan',
      description: 'Additional checks specific to Juniper switches.',
      checks: [
        {
          name: 'Optic Modules',
          id: 'optics_health',
          description:
            'Validates Rx/Tx power levels on SFP/SFP+ transceivers. Rx thresholds: pass >= -20 dBm, warn -25 to -20 dBm, fail < -25 dBm. Tx thresholds: pass >= -8 dBm, warn -12 to -8 dBm, fail < -12 dBm.',
          pass: 'All optics within acceptable power levels',
          warn: 'Some ports have low power readings',
          fail: 'One or more ports below failure threshold',
        },
        {
          name: 'Cable Tests (Optional)',
          id: 'cable_tests',
          description:
            'Runs TDR cable diagnostics on copper ports. Requires write access and site group membership.',
          pass: 'All cable pairs report normal',
          warn: '',
          fail: 'One or more cable pairs report a fault',
        },
      ],
    },
    {
      title: 'Virtual Chassis Checks',
      icon: 'device_hub',
      description:
        'Checks for switch Virtual Chassis (VC) members. Applied per-member when a VC is detected.',
      checks: [
        {
          name: 'Member Present',
          id: 'member_present',
          description: 'Verifies each VC member is present and has an active role.',
          pass: 'Member is present with an active role',
          warn: '',
          fail: 'Member is not present',
        },
        {
          name: 'Firmware Match',
          id: 'firmware_match',
          description: "Checks if the member's firmware matches the primary switch firmware.",
          pass: 'Firmware matches primary',
          warn: '',
          fail: 'Firmware mismatch',
        },
        {
          name: 'VC Ports UP',
          id: 'vc_ports_up',
          description: 'Checks the number of VC interconnect links that are UP per member.',
          pass: '>= 2 VC links are UP',
          warn: '',
          fail: '< 2 VC links are UP',
        },
      ],
    },
    {
      title: 'Gateway Checks',
      icon: 'security',
      description: 'Additional checks specific to SRX and SSR gateways.',
      checks: [
        {
          name: 'WAN Port Status',
          id: 'wan_port_status',
          description: 'Checks whether all configured WAN ports are UP.',
          pass: 'All WAN ports are UP',
          warn: 'Some WAN ports are DOWN',
          fail: 'No WAN ports are UP',
        },
        {
          name: 'LAN Port Status',
          id: 'lan_port_status',
          description: 'Checks whether all configured LAN ports are UP.',
          pass: 'All LAN ports are UP',
          warn: 'Some LAN ports are DOWN',
          fail: 'No LAN ports are UP',
        },
        {
          name: 'Optic Modules',
          id: 'optics_health_gw',
          description:
            'Same thresholds as switch optics. Rx: pass >= -20 dBm, warn -25 to -20 dBm, fail < -25 dBm. Tx: pass >= -8 dBm, warn -12 to -8 dBm, fail < -12 dBm.',
          pass: 'All optics within acceptable power levels',
          warn: 'Some ports have low power readings',
          fail: 'One or more ports below failure threshold',
        },
      ],
    },
    {
      title: 'Gateway Cluster Checks',
      icon: 'sync_alt',
      description:
        'Checks for HA gateway clusters. Applied per-node when a cluster is detected.',
      checks: [
        {
          name: 'Node Connected',
          id: 'node_connected',
          description: 'Verifies each cluster node is connected.',
          pass: 'Node is connected',
          warn: '',
          fail: 'Node is not connected',
        },
        {
          name: 'Firmware Match',
          id: 'firmware_match_gw',
          description: "Checks if the node's firmware matches the primary gateway firmware.",
          pass: 'Firmware matches primary',
          warn: '',
          fail: 'Firmware mismatch',
        },
      ],
    },
  ];
}
