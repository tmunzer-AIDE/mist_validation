import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import {
  MatDialogRef,
  MatDialogTitle,
  MatDialogContent,
  MatDialogActions,
} from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

@Component({
  selector: 'app-two-factor-dialog',
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatDialogTitle,
    MatDialogContent,
    MatDialogActions,
    MatFormFieldModule,
    MatInputModule,
  ],
  template: `
    <h2 mat-dialog-title>Two-Factor Authentication</h2>
    <div mat-dialog-content>
      <mat-form-field class="full-width" appearance="outline" style="margin-top: 5px;">
        <mat-label>Verification code</mat-label>
        <input
          matInput
          type="text"
          inputmode="numeric"
          [(ngModel)]="code"
          (keydown.enter)="submit()"
          cdkFocusInitial
        />
      </mat-form-field>
    </div>
    <div mat-dialog-actions align="end">
      <button mat-button (click)="dialogRef.close()">Cancel</button>
      <button mat-flat-button color="primary" (click)="submit()" [disabled]="!code.trim()">
        Verify
      </button>
    </div>
  `,
  styles: [
    `
      .full-width {
        width: 100%;
      }
    `,
  ],
})
export class TwoFactorDialogComponent {
  dialogRef = inject(MatDialogRef<TwoFactorDialogComponent>);
  code = '';

  submit(): void {
    if (this.code.trim()) {
      this.dialogRef.close(this.code.trim());
    }
  }
}
