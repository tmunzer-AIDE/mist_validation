import { Component, EventEmitter, Output } from '@angular/core';
import {
  PageShellComponent,
  ShellRoute,
} from '../../shared/components/page-shell/page-shell.component';

interface CheckDef {
  name: string;
  scope: string;
  pass: string;
  warn: string;
  fail: string;
}

interface CheckSection {
  category: string;
  checks: CheckDef[];
}

@Component({
  selector: 'app-validation-reference',
  standalone: true,
  imports: [PageShellComponent],
  templateUrl: './validation-reference.component.html',
  styleUrl: './validation-reference.component.scss',
})
export class ValidationReferenceComponent {
  @Output() navigate = new EventEmitter<ShellRoute>();

  onShellNavigate(route: ShellRoute): void {
    if (route !== 'validation_reference') {
      this.navigate.emit(route);
    }
  }

  sections: CheckSection[] = [
    {
      category: 'Site',
      checks: [
        {
          name: 'Template Variables',
          scope: 'All Jinja2 vars in RF, Network, Gateway and Site templates resolve in site settings',
          pass: 'All variables defined',
          warn: '—',
          fail: 'Any variable undefined',
        },
        {
          name: 'Device Events',
          scope: '24-hour event correlation across the site',
          pass: 'No active alarms',
          warn: 'Cleared events present',
          fail: 'Active uncleared trigger events',
        },
      ],
    },
    {
      category: 'Per-device — common',
      checks: [
        {
          name: 'Device Name',
          scope: 'AP / Switch / Gateway',
          pass: 'Name set',
          warn: '—',
          fail: 'Empty / default',
        },
        {
          name: 'Firmware Version',
          scope: 'AP / Switch / Gateway',
          pass: 'Matches recommended (or auto-upgrade target)',
          warn: 'Other supported version',
          fail: 'Tagged deprecated or alpha',
        },
        {
          name: 'Connection Status',
          scope: 'AP / Switch / Gateway',
          pass: 'Connected to Mist Cloud',
          warn: '—',
          fail: 'Disconnected',
        },
        {
          name: 'Configuration Status',
          scope: 'AP / Switch / Gateway',
          pass: 'Latest config event succeeded',
          warn: '—',
          fail: 'Last config event failed',
        },
      ],
    },
    {
      category: 'Access Points',
      checks: [
        {
          name: 'Eth0 Port Speed',
          scope: 'AP uplink',
          pass: '≥ 1 Gbps',
          warn: '< 1 Gbps',
          fail: '—',
        },
        {
          name: 'Power Constrained',
          scope: 'AP PoE',
          pass: 'Not power-limited',
          warn: 'Power limited',
          fail: '—',
        },
        {
          name: 'LLDP Neighbor',
          scope: 'AP uplink',
          pass: 'Neighbor reported',
          warn: '—',
          fail: '—',
        },
      ],
    },
    {
      category: 'Switches & Gateways',
      checks: [
        {
          name: 'Optic Modules — Rx',
          scope: 'SFP/SFP+ Rx power',
          pass: '≥ −20 dBm',
          warn: '−25 to −20 dBm',
          fail: '< −25 dBm',
        },
        {
          name: 'Optic Modules — Tx',
          scope: 'SFP/SFP+ Tx power',
          pass: '≥ −8 dBm',
          warn: '−12 to −8 dBm',
          fail: '< −12 dBm',
        },
        {
          name: 'Cable Tests (optional)',
          scope: 'TDR diagnostics on copper switch ports',
          pass: 'All pairs report normal',
          warn: '—',
          fail: 'One or more pairs report a fault',
        },
        {
          name: 'Config Command Errors (optional)',
          scope: 'EX/QFX switches and SRX gateways',
          pass: 'No configuration errors',
          warn: 'One or more configuration errors detected',
          fail: '—',
        },
      ],
    },
    {
      category: 'Gateways',
      checks: [
        {
          name: 'WAN Port Status',
          scope: 'Configured WAN ports',
          pass: 'All UP',
          warn: '—',
          fail: 'Any DOWN',
        },
        {
          name: 'LAN Port Status',
          scope: 'Configured LAN ports',
          pass: 'All UP',
          warn: 'Any admin DOWN',
          fail: 'Operational DOWN',
        },
      ],
    },
    {
      category: 'Virtual Chassis',
      checks: [
        {
          name: 'Member Present',
          scope: 'Each VC member',
          pass: 'Active role',
          warn: '—',
          fail: 'Missing / inactive',
        },
        {
          name: 'Firmware Match',
          scope: 'Each VC member',
          pass: 'Matches primary',
          warn: '—',
          fail: 'Mismatch',
        },
        {
          name: 'VC Ports UP',
          scope: 'Per member',
          pass: '≥ 2 interconnects UP',
          warn: '1 UP',
          fail: '0 UP',
        },
      ],
    },
    {
      category: 'HA Gateway Cluster',
      checks: [
        {
          name: 'Node Connected',
          scope: 'Each cluster node',
          pass: 'Connected',
          warn: '—',
          fail: 'Disconnected',
        },
        {
          name: 'Firmware Match',
          scope: 'Cluster node',
          pass: 'Matches primary',
          warn: '—',
          fail: 'Mismatch',
        },
      ],
    },
    {
      category: 'Synthetic tests (Marvis Minis)',
      checks: [
        {
          name: 'Marvis Minis — DHCP',
          scope: 'Per AP × VLAN — opt-in',
          pass: 'DHCP lease obtained',
          warn: '—',
          fail: 'DHCP unresponsive or rejected',
        },
        {
          name: 'Marvis Minis — ARP',
          scope: 'Per AP × VLAN — opt-in',
          pass: 'ARP resolved gateway MAC',
          warn: '—',
          fail: 'ARP timed out or no entry',
        },
        {
          name: 'Marvis Minis — DNS',
          scope: 'Per AP × VLAN — opt-in',
          pass: 'All test URLs resolved',
          warn: '—',
          fail: 'One or more URLs failed to resolve',
        },
        {
          name: 'Marvis Minis — CURL',
          scope: 'Per AP × VLAN — opt-in',
          pass: 'All target URLs returned a 2xx/3xx',
          warn: '—',
          fail: 'One or more URLs unreachable',
        },
      ],
    },
  ];
}
