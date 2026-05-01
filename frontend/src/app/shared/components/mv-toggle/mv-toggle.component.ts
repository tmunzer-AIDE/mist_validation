import { Component, EventEmitter, Input, Output } from '@angular/core';

@Component({
  selector: 'mv-toggle',
  standalone: true,
  templateUrl: './mv-toggle.component.html',
  styleUrl: './mv-toggle.component.scss',
})
export class MvToggleComponent {
  @Input() checked = false;
  @Input() disabled = false;
  @Output() change = new EventEmitter<{ checked: boolean }>();

  toggle(): void {
    if (this.disabled) return;
    const next = !this.checked;
    this.checked = next;
    this.change.emit({ checked: next });
  }
}
