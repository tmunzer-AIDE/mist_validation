import { Component, computed, input } from '@angular/core';
import { NgClass } from '@angular/common';

function statusLabel(s: string): string {
  switch (s) {
    case 'pass':
      return 'Pass';
    case 'fail':
      return 'Fail';
    case 'warn':
      return 'Warning';
    case 'pending':
      return 'Pending';
    case 'running':
      return 'Running';
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    default:
      return 'Info';
  }
}

@Component({
  selector: 'app-status-badge',
  standalone: true,
  imports: [NgClass],
  template: `<span class="badge" [ngClass]="classes()">{{ displayLabel() }}</span>`,
  styleUrl: './status-badge.component.scss',
})
export class StatusBadgeComponent {
  status = input<string>('info');
  label = input<string>('');
  size = input<'sm' | 'md'>('md');

  displayLabel = computed(() => this.label() || statusLabel(this.status()));
  classes = computed(() => ({
    [`status-${this.status()}`]: true,
    'badge--sm': this.size() === 'sm',
  }));
}
