import { Component, computed, input } from '@angular/core';
import { MatChipsModule } from '@angular/material/chips';

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
  imports: [MatChipsModule],
  template: `
    <mat-chip [class]="'status-' + status()">{{ displayLabel() }}</mat-chip>
  `,
})
export class StatusBadgeComponent {
  status = input<string>('info');
  label = input<string>('');

  displayLabel = computed(() => this.label() || statusLabel(this.status()));
}
