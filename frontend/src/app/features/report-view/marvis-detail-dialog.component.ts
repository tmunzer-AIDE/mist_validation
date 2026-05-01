import { Component, Inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatExpansionModule } from '@angular/material/expansion';
import { StatusBadgeComponent } from '../../shared/components/status-badge/status-badge.component';
import { MarvisAp, MarvisVlan, MarvisTest } from './marvis-matrix.component';

export interface MarvisDetailData {
  ap: MarvisAp;
  vlan: MarvisVlan;
}

interface KvRow {
  label: string;
  value: string;
  mono?: boolean;
}

interface ArpRow {
  ip: string;
  mac: string;
  latency: string;
}

interface DnsRow {
  url: string;
  resolved: string;  // joined IPs OR error message
  isError: boolean;
  dns_ip: string;
  latency: string;
}

interface CurlRow {
  url: string;
  server_ip: string;
  response: string;  // status code OR error message
  isError: boolean;
  latency: string;
}

@Component({
  selector: 'app-marvis-detail-dialog',
  standalone: true,
  imports: [
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatExpansionModule,
    StatusBadgeComponent,
  ],
  templateUrl: './marvis-detail-dialog.component.html',
  styleUrl: './marvis-detail-dialog.component.scss',
})
export class MarvisDetailDialogComponent {
  constructor(
    public dialogRef: MatDialogRef<MarvisDetailDialogComponent>,
    @Inject(MAT_DIALOG_DATA) public data: MarvisDetailData,
  ) {}

  formatJson(detail: Record<string, unknown>): string {
    try {
      return JSON.stringify(detail, null, 2);
    } catch {
      return String(detail);
    }
  }

  trackByTest(_idx: number, test: MarvisTest): string {
    return test.test_type;
  }

  // ── Per-test structured detail extractors ───────────────────────────────
  // Each returns an empty array (or empty string) when there's nothing useful
  // to render — the template hides the section in that case.

  dhcpFields(test: MarvisTest): KvRow[] {
    const dhcp = (test.detail?.['dhcpv4'] as Record<string, unknown> | undefined) ?? {};
    const rows: KvRow[] = [];
    const push = (label: string, value: unknown, mono = false) => {
      if (value !== undefined && value !== null && value !== '') {
        rows.push({ label, value: String(value), mono });
      }
    };
    push('IP / Subnet', dhcp['ip'], true);
    push('Server', dhcp['server'], true);
    push('Gateway', dhcp['gw'], true);
    const dns = dhcp['dns'];
    if (Array.isArray(dns) && dns.length) push('DNS', dns.join(', '), true);
    if (typeof dhcp['lease_time'] === 'number') {
      push('Lease time', this.formatLeaseTime(dhcp['lease_time'] as number));
    }
    if (typeof dhcp['offer_latency'] === 'number') push('Offer latency', `${dhcp['offer_latency']}ms`);
    if (typeof dhcp['ack_latency'] === 'number') push('Ack latency', `${dhcp['ack_latency']}ms`);
    if (test.status !== 'pass') {
      push('State', dhcp['state']);
      push('Notes', dhcp['summary']);
    }
    return rows;
  }

  arpRows(test: MarvisTest): ArpRow[] {
    const ips = (test.detail?.['ips'] as unknown[] | undefined) ?? [];
    return ips
      .filter((row): row is Record<string, unknown> => row !== null && typeof row === 'object')
      .map((row) => ({
        ip: String(row['ip'] ?? '—'),
        mac: String(row['mac'] ?? '—'),
        latency: typeof row['latency'] === 'number' ? `${row['latency']}ms` : '—',
      }));
  }

  dnsRows(test: MarvisTest): DnsRow[] {
    const urls = (test.detail?.['urls'] as unknown[] | undefined) ?? [];
    return urls
      .filter((row): row is Record<string, unknown> => row !== null && typeof row === 'object')
      .map((row) => {
        const ips = row['ips'];
        const error = typeof row['error'] === 'string' ? row['error'] : '';
        return {
          url: String(row['url'] ?? '—'),
          resolved: error || (Array.isArray(ips) ? ips.join(', ') : '—'),
          isError: !!error,
          dns_ip: String(row['dns_ip'] ?? '—'),
          latency: typeof row['latency'] === 'number' ? `${row['latency']}ms` : '—',
        };
      });
  }

  curlRows(test: MarvisTest): CurlRow[] {
    const urls = (test.detail?.['urls'] as unknown[] | undefined) ?? [];
    return urls
      .filter((row): row is Record<string, unknown> => row !== null && typeof row === 'object')
      .map((row) => {
        const error = typeof row['error'] === 'string' ? row['error'] : '';
        // Trim noisy "Failed http get: Get \"…\":" prefix to keep the cell readable.
        const trimmedError = error.replace(/^Failed http get:\s*Get\s*"[^"]*":\s*/, '');
        return {
          url: String(row['url'] ?? '—'),
          server_ip: String(row['server_ip'] ?? '—'),
          response: error ? trimmedError : String(row['response'] ?? '—'),
          isError: !!error,
          latency: typeof row['latency'] === 'number' ? `${row['latency']}ms` : '—',
        };
      });
  }

  // CURL test_detail's per-URL `client_ip` is the same value across every URL
  // (it's the AP's leased IP for the run), so surface it once above the table
  // rather than as a redundant column.
  curlClientIp(test: MarvisTest): string {
    const urls = (test.detail?.['urls'] as unknown[] | undefined) ?? [];
    for (const row of urls) {
      if (row && typeof row === 'object' && 'client_ip' in row) {
        const ip = (row as Record<string, unknown>)['client_ip'];
        if (ip) return String(ip);
      }
    }
    return '';
  }

  private formatLeaseTime(seconds: number): string {
    if (seconds % 86400 === 0) return `${seconds / 86400}d`;
    if (seconds % 3600 === 0) return `${seconds / 3600}h`;
    if (seconds % 60 === 0) return `${seconds / 60}m`;
    return `${seconds}s`;
  }

  close(): void {
    this.dialogRef.close();
  }
}
