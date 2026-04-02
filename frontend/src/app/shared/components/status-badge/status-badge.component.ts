import { Component, computed, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

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

function statusIcon(s: string): string {
  switch (s) {
    case 'pass':
    case 'completed':
      return 'check_circle';
    case 'fail':
    case 'failed':
      return 'cancel';
    case 'warn':
      return 'warning';
    case 'pending':
      return 'schedule';
    case 'running':
      return 'hourglass_empty';
    default:
      return 'info';
  }
}

@Component({
  selector: 'app-status-badge',
  standalone: true,
  imports: [MatIconModule],
  templateUrl: './status-badge.component.html',
  styleUrl: './status-badge.component.scss',
})
export class StatusBadgeComponent {
  status = input<string>('info');
  label = input<string>('');

  displayLabel = computed(() => this.label() || statusLabel(this.status()));
  icon = computed(() => statusIcon(this.status()));
}
