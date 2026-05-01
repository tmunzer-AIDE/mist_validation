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

  close(): void {
    this.dialogRef.close();
  }
}
