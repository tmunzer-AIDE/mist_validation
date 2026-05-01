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
    // Don't mutate the @Input — emit the new value and let the parent update [checked].
    // All current callers use one-way [checked]+(change) binding, so this is safe.
    this.change.emit({ checked: !this.checked });
  }
}
