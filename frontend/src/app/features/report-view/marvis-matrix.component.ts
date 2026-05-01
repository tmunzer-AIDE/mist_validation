import { Component, EventEmitter, Output, computed, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';

export type MarvisCellStatus = 'pass' | 'warn' | 'fail' | 'info' | 'not_tested' | 'pending';

export interface MarvisTest {
  test_type: 'DHCP' | 'ARP' | 'DNS' | 'CURL' | string;
  status: 'pass' | 'fail' | 'warn' | 'info';
  summary: string;
  detail: Record<string, unknown>;
}

export interface MarvisVlan {
  vlan: number | string;
  status: 'pass' | 'warn' | 'fail' | 'info' | 'not_tested';
  has_pcap: boolean;
  pcap_url: string | null;
  tests: MarvisTest[];
}

export interface MarvisAp {
  ap_mac: string;
  ap_name: string;
  switch_name: string;
  switch_port: string;
  vlans: MarvisVlan[];
}

export interface MarvisResult {
  status: 'completed' | 'trigger_failed' | 'timeout';
  test_id: string | null;
  duration_seconds: number;
  started_at: number;
  result: string | null;
  summary: { pass: number; fail: number; warn: number };
  trigger_error: string | null;
  ap_results: MarvisAp[];
}

export interface MarvisLiveSnapshot {
  test_id: string;
  progress: number;
  ap_count_done: number;
  ap_count_total: number;
  ap_results: MarvisAp[];
}

export interface MarvisCellClick {
  ap: MarvisAp;
  vlan: MarvisVlan;
}

@Component({
  selector: 'app-marvis-matrix',
  standalone: true,
  imports: [MatIconModule, MatProgressSpinnerModule, MatTooltipModule],
  templateUrl: './marvis-matrix.component.html',
  styleUrl: './marvis-matrix.component.scss',
})
export class MarvisMatrixComponent {
  data = input.required<MarvisResult | MarvisLiveSnapshot | null>();
  // True when this is a partial in-progress snapshot (from WS), false for a final result.
  live = input<boolean>(false);

  @Output() cellClick = new EventEmitter<MarvisCellClick>();

  apResults = computed<MarvisAp[]>(() => this.data()?.ap_results ?? []);

  vlanIds = computed<(number | string)[]>(() => {
    const ids = new Set<number | string>();
    for (const ap of this.apResults()) {
      for (const v of ap.vlans ?? []) {
        if (v.vlan !== null && v.vlan !== undefined) ids.add(v.vlan);
      }
    }
    // Numeric VLAN IDs sort numerically. Non-numeric tokens (e.g. "untagged",
    // "native") would produce NaN under Number() and destabilize the order;
    // fall back to lexicographic for those, with non-numerics grouped after
    // numerics so the common case is unaffected.
    return Array.from(ids).sort((a, b) => {
      const an = Number(a);
      const bn = Number(b);
      const aNum = !Number.isNaN(an);
      const bNum = !Number.isNaN(bn);
      if (aNum && bNum) return an - bn;
      if (aNum) return -1;
      if (bNum) return 1;
      return String(a).localeCompare(String(b));
    });
  });

  vlanByAp(ap: MarvisAp, vlanId: number | string): MarvisVlan | null {
    return ap.vlans.find((v) => v.vlan === vlanId) ?? null;
  }

  // Cell display status:
  //  - vlan exists with non-empty tests → vlan.status (pass/warn/fail/info/not_tested)
  //  - vlan exists with empty tests in live mode → 'pending'
  //  - vlan missing entirely from this AP → 'pending' (live) or 'not_tested' (final)
  cellStatus(ap: MarvisAp, vlanId: number | string): MarvisCellStatus {
    const vlan = this.vlanByAp(ap, vlanId);
    if (!vlan) return this.live() ? 'pending' : 'not_tested';
    if (vlan.status === 'not_tested' && this.live() && vlan.tests.length === 0) {
      return 'pending';
    }
    return vlan.status;
  }

  cellTooltip(ap: MarvisAp, vlanId: number | string): string {
    const vlan = this.vlanByAp(ap, vlanId);
    if (!vlan) return `VLAN ${vlanId} · ${this.live() ? 'waiting…' : 'not tested'}`;
    if (!vlan.tests.length) return `VLAN ${vlanId} · not tested`;
    const failures = vlan.tests.filter((t) => t.status === 'fail').map((t) => t.test_type);
    if (failures.length) return `VLAN ${vlanId} · failed: ${failures.join(', ')}`;
    const warns = vlan.tests.filter((t) => t.status === 'warn').map((t) => t.test_type);
    if (warns.length) return `VLAN ${vlanId} · warn: ${warns.join(', ')}`;
    const passed = vlan.tests.filter((t) => t.status === 'pass').length;
    const skipped = vlan.tests.filter((t) => t.status === 'info').length;
    if (skipped) return `VLAN ${vlanId} · ${passed}/${vlan.tests.length} passed, ${skipped} not validated`;
    return `VLAN ${vlanId} · all tests passed`;
  }

  onCellClick(ap: MarvisAp, vlanId: number | string): void {
    if (!this.isCellClickable(ap, vlanId)) return;
    const vlan = this.vlanByAp(ap, vlanId);
    if (!vlan || !vlan.tests.length) return;
    this.cellClick.emit({ ap, vlan });
  }

  // True when a cell has actionable content (a drawer-worthy result).
  // `not_tested`, `pending`, and `info` cells are inert — no drawer to open.
  isCellClickable(ap: MarvisAp, vlanId: number | string): boolean {
    const s = this.cellStatus(ap, vlanId);
    return s !== 'not_tested' && s !== 'pending' && s !== 'info';
  }

  iconFor(status: MarvisCellStatus): string {
    return {
      pass: 'check_circle',
      fail: 'cancel',
      warn: 'warning',
      info: 'remove',
      not_tested: 'remove',
      pending: 'hourglass_empty',
    }[status];
  }
}
