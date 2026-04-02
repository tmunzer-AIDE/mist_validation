import { Component, EventEmitter, OnInit, Output, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatCardModule } from '@angular/material/card';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatIconModule } from '@angular/material/icon';
import { ApiService } from '../../core/services/api.service';
import { AuthInfo } from '../../app.component';
import { TwoFactorDialogComponent } from './two-factor-dialog.component';

interface LoginResponse {
  method: 'token' | 'credentials';
  cloud: string;
  host: string;
  user_email: string;
  token_name: string;
  orgs: { id: string; name: string; role: string }[];
  // 2FA fields (returned when 2FA is required)
  two_factor_required?: boolean;
  two_factor_passed?: boolean;
}

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatButtonToggleModule,
    MatSelectModule,
    MatProgressSpinnerModule,
    MatIconModule,
    MatDialogModule,
  ],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
})
export class LoginComponent implements OnInit {
  @Output() authenticated = new EventEmitter<AuthInfo>();

  private fb = inject(FormBuilder);
  private api = inject(ApiService);
  private dialog = inject(MatDialog);

  clouds = signal<{ value: string; label: string }[]>([]);
  authMode = signal<'token' | 'credentials'>('credentials');
  loading = signal(false);
  errorMsg = signal('');

  form = this.fb.group({
    cloud: ['Global 01', Validators.required],
    token: [''],
    email: [''],
    password: [''],
  });

  ngOnInit(): void {
    this.api.get<{ value: string; label: string }[]>('clouds').subscribe({
      next: (list) => this.clouds.set(list),
    });
  }

  setMode(mode: 'token' | 'credentials'): void {
    this.authMode.set(mode);
    this.errorMsg.set('');
    this.form.patchValue({ token: '', email: '', password: '' });
  }

  onSubmit(): void {
    if (this.loading()) return;
    this.errorMsg.set('');

    const { cloud, token, email, password } = this.form.value;
    const mode = this.authMode();

    if (mode === 'token' && !token?.trim()) {
      this.errorMsg.set('Please enter an API token.');
      return;
    }
    if (mode === 'credentials' && (!email?.trim() || !password?.trim())) {
      this.errorMsg.set('Please enter your email and password.');
      return;
    }

    // Resolve host from cloud selection
    const cloudVal = cloud ?? 'Global 01';
    const body =
      mode === 'token'
        ? { cloud: cloudVal, token: token?.trim() }
        : { cloud: cloudVal, email: email?.trim(), password };

    this._doLogin(body, cloudVal);
  }

  private _doLogin(
    body: Record<string, unknown>,
    cloud: string,
  ): void {
    this.loading.set(true);
    this.api.post<LoginResponse>('auth/login', body).subscribe({
      next: (res) => {
        // Check for 2FA
        if (res.two_factor_required && !res.two_factor_passed) {
          this.loading.set(false);
          this._open2FA(body, cloud);
          return;
        }

        this.loading.set(false);
        this.authenticated.emit({
          user_email: res.user_email,
          token_name: res.token_name,
          orgs: res.orgs,
          cloud: res.cloud,
          host: res.host,
          method: res.method,
        });
      },
      error: (err) => {
        this.loading.set(false);
        const msg =
          err?.error?.detail ?? err?.error?.message ?? 'Authentication failed. Please try again.';
        this.errorMsg.set(msg as string);
      },
    });
  }

  private _open2FA(
    loginBody: Record<string, unknown>,
    cloud: string,
  ): void {
    const dialogRef = this.dialog.open(TwoFactorDialogComponent, {
      width: '360px',
      disableClose: true,
    });

    dialogRef.afterClosed().subscribe((code: string | undefined) => {
      if (!code) return; // user cancelled
      this._doLogin({ ...loginBody, two_factor: code }, cloud);
    });
  }
}
