// Shared helpers used by report-view and org-report-view to avoid duplication.

export function worstStatus(items: { status: string }[]): string {
  if (!items.length) return 'info';
  if (items.some((i) => i.status === 'fail')) return 'fail';
  if (items.some((i) => i.status === 'warn')) return 'warn';
  return 'pass';
}

const CHECK_LABELS: Record<string, string> = {
  name_defined: 'Device name',
  firmware_version: 'Firmware',
  connection_status: 'Connection',
  config_status: 'Config push',
  eth0_port_speed: 'Eth0 speed',
  power_constrained: 'Power',
  lldp_neighbor: 'LLDP',
  optics_health: 'Optics',
  cable_tests: 'Cable tests',
  config_errors: 'Config errors',
  wan_port_status: 'WAN ports',
  lan_port_status: 'LAN ports',
  member_present: 'VC member',
  firmware_match: 'Firmware match',
  vc_ports_up: 'VC ports',
  node_connected: 'HA node',
};

export function checkLabel(id: string): string {
  return CHECK_LABELS[id] ?? id.replace(/_/g, ' ');
}

export function deviceTypeLabel(t: string): string {
  if (t === 'ap') return 'Access Points';
  if (t === 'switch') return 'Switches';
  if (t === 'gateway') return 'Gateways';
  return t;
}

export function deviceTypeIcon(t: string): string {
  if (t === 'ap') return 'wifi';
  if (t === 'switch') return 'lan';
  if (t === 'gateway') return 'router';
  return 'devices';
}
