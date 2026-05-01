import {
  Component,
  EventEmitter,
  OnDestroy,
  OnInit,
  Output,
  computed,
  input,
  signal,
} from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import {
  PageShellComponent,
  ShellRoute,
} from '../../shared/components/page-shell/page-shell.component';

interface ProgressStep {
  id: string;
  label: string;
  status: string;
  message: string;
}

interface RunningReport {
  id: string;
  scope: string;
  org_name: string;
  site_name: string;
  status: string;
  created_at: string;
  progress: {
    overall_completed: number;
    overall_total: number;
    steps: ProgressStep[];
    eta_seconds?: number;
  };
}

// Map step IDs to phase labels (covers both site and org reports).
const PHASE_BY_STEP: Record<string, string> = {
  // Site report
  site_info: 'Setup',
  templates: 'Setup',
  variables: 'Site checks',
  config_events: 'Site checks',
  device_events: 'Site checks',
  aps: 'Per-device',
  switches: 'Per-device',
  gateways: 'Per-device',
  cable_tests: 'Diagnostics',
  config_errors: 'Diagnostics',
  marvis_minis: 'Diagnostics',
  // Org report
  preflight: 'Setup',
  org_data: 'Setup',
  device_stats: 'Inventory',
  port_stats: 'Inventory',
  firmware: 'Inventory',
  ap_validation: 'Per-device',
  sw_validation: 'Per-device',
  gw_validation: 'Per-device',
};

const PHASE_ORDER = ['Setup', 'Inventory', 'Site checks', 'Per-device', 'Diagnostics', 'Other'];

interface StepGroup {
  phase: string;
  items: ProgressStep[];
  doneCount: number;
  total: number;
  done: boolean;
  running: boolean;
}

@Component({
  selector: 'app-running-screen',
  standalone: true,
  imports: [MatIconModule, PageShellComponent],
  templateUrl: './running-screen.component.html',
  styleUrl: './running-screen.component.scss',
})
export class RunningScreenComponent implements OnInit, OnDestroy {
  report = input.required<RunningReport>();

  @Output() navigate = new EventEmitter<ShellRoute>();

  now = signal(Date.now());
  private timer: ReturnType<typeof setInterval> | undefined;

  ngOnInit(): void {
    this.timer = setInterval(() => this.now.set(Date.now()), 1000);
  }

  ngOnDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }

  isComplete = computed(() => {
    const s = this.report().status;
    return s === 'completed' || s === 'failed';
  });

  scopeLabel = computed(() => {
    const r = this.report();
    return r.scope === 'org' ? r.org_name || 'Organization-wide' : r.site_name || 'Site';
  });

  progressPercent = computed(() => {
    const p = this.report().progress;
    if (!p || !p.overall_total) return 0;
    return Math.min(100, Math.round((p.overall_completed / p.overall_total) * 100));
  });

  steps = computed<ProgressStep[]>(() => this.report().progress?.steps ?? []);

  doneStepCount = computed(() => this.steps().filter((s) => s.status === 'completed').length);
  totalSteps = computed(() => this.steps().length);

  currentStep = computed<ProgressStep | null>(() => {
    const s = this.steps();
    return s.find((x) => x.status === 'running') ?? null;
  });

  groupedSteps = computed<StepGroup[]>(() => {
    const groups = new Map<string, ProgressStep[]>();
    for (const s of this.steps()) {
      const phase = PHASE_BY_STEP[s.id] ?? 'Other';
      if (!groups.has(phase)) groups.set(phase, []);
      groups.get(phase)!.push(s);
    }
    return PHASE_ORDER.filter((p) => groups.has(p)).map((phase) => {
      const items = groups.get(phase)!;
      const doneCount = items.filter((s) => s.status === 'completed').length;
      return {
        phase,
        items,
        doneCount,
        total: items.length,
        done: doneCount === items.length,
        running: items.some((s) => s.status === 'running'),
      };
    });
  });

  elapsedSeconds = computed(() => {
    const start = new Date(this.report().created_at).getTime();
    if (isNaN(start)) return 0;
    return Math.max(0, Math.floor((this.now() - start) / 1000));
  });

  // Re-anchor only when eta_seconds itself changes. The parent polls the report API
  // every 5s and re-sets `report()` even when no WS broadcast has fired, which would
  // otherwise reset capturedAt to "now" with the stale persisted eta_seconds — making
  // the displayed countdown bounce back up every 5s instead of decreasing. With the
  // equal comparator, an unchanged eta_seconds keeps the original anchor so the
  // timer-driven countdown ticks down cleanly between real WS updates.
  private etaAnchor = computed<{ seconds: number | null; capturedAt: number }>(
    () => {
      const eta = this.report().progress?.eta_seconds;
      return {
        seconds: typeof eta === 'number' ? eta : null,
        capturedAt: Date.now(),
      };
    },
    { equal: (a, b) => a.seconds === b.seconds },
  );

  etaSeconds = computed<number | null>(() => {
    if (this.isComplete()) return null;
    const anchor = this.etaAnchor();
    if (anchor.seconds === null) return null;
    const drift = Math.floor((this.now() - anchor.capturedAt) / 1000);
    return Math.max(0, anchor.seconds - drift);
  });

  fmtTime(sec: number): string {
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${s.toString().padStart(2, '0')}s`;
  }

  // Progress ring stroke math
  ringSize = 72;
  ringRadius = (this.ringSize - 8) / 2;
  ringCircumference = 2 * Math.PI * this.ringRadius;
  ringDash = computed(() => (this.progressPercent() / 100) * this.ringCircumference);

  shellTitle = computed(() => {
    const r = this.report();
    if (r.status === 'failed') return 'Validation failed';
    if (r.status === 'completed') return 'Validation complete';
    return 'Generating report';
  });

  shellSubtitle = computed(() => {
    const r = this.report();
    return `${this.scopeLabel()} · run ${r.id.slice(0, 8)}`;
  });
}
