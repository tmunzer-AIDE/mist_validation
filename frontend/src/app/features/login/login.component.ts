import { Component, EventEmitter, Output, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatIconModule } from '@angular/material/icon';
import { ApiService } from '../../core/services/api.service';
import { AuthInfo } from '../../app.component';

const CLOUDS = [
  { value: 'global_01', label: 'Global 01 (api.mist.com)' },
  { value: 'emea_01', label: 'EU (api.eu.mist.com)' },
  { value: 'global_02', label: 'Global 02 (api.gc1.mist.com)' },
  { value: 'global_03', label: 'Global 03 (api.ac2.mist.com)' },
  { value: 'global_04', label: 'Global 04 (api.gc2.mist.com)' },
  { value: 'global_05', label: 'Global 05 (api.gc4.mist.com)' },
  { value: 'emea_02', label: 'EMEA 02 (api.gc3.mist.com)' },
  { value: 'emea_03', label: 'EMEA 03 (api.ac6.mist.com)' },
  { value: 'apac_01', label: 'APAC 01 (api.ac5.mist.com)' },
  { value: 'apac_02', label: 'APAC 02 (api.gc5.mist.com)' },
];

interface AuthVerifyResponse {
  user_id: string;
  user_email: string;
  orgs: { id: string; name: string }[];
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
  ],
  styles: [
    `
      .login-container {
        display: flex;
        justify-content: center;
        align-items: center;
        min-height: 100vh;
        background: #f5f5f5;
      }
      .login-card {
        width: 440px;
        padding: 16px;
      }
      .full-width {
        width: 100%;
      }
      .error-msg {
        color: #f44336;
        margin-bottom: 8px;
        font-size: 14px;
      }
      .toggle-group {
        width: 100%;
        margin-bottom: 16px;
      }
      .toggle-group mat-button-toggle {
        flex: 1;
      }
      .logo-row {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 4px;
      }
      .logo-icon {
        font-size: 36px;
        width: 36px;
        height: 36px;
        color: #1976d2;
      }
      .subtitle {
        color: rgba(0, 0, 0, 0.54);
        font-size: 14px;
        margin-bottom: 24px;
      }
      .spinner-row {
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 12px;
        margin-top: 4px;
      }
    `,
  ],
  template: `
    <div class="login-container">
      <mat-card class="login-card">
        <mat-card-content>
          <div class="logo-row">
            <mat-icon class="logo-icon">verified_user</mat-icon>
            <div>
              <div style="font-size: 20px; font-weight: 500;">Post-Validation Report</div>
              <div class="subtitle">Mist Network Validation Tool</div>
            </div>
          </div>

          <form [formGroup]="form" (ngSubmit)="onSubmit()">
            <!-- Auth mode toggle -->
            <mat-button-toggle-group
              class="toggle-group"
              [value]="authMode()"
              (change)="setMode($event.value)"
              aria-label="Authentication mode"
            >
              <mat-button-toggle value="token">API Token</mat-button-toggle>
              <mat-button-toggle value="credentials">Username & Password</mat-button-toggle>
            </mat-button-toggle-group>

            <!-- Cloud selection -->
            <mat-form-field class="full-width" appearance="outline">
              <mat-label>Cloud Region</mat-label>
              <mat-select formControlName="cloud">
                @for (cloud of clouds; track cloud.value) {
                  <mat-option [value]="cloud.value">{{ cloud.label }}</mat-option>
                }
              </mat-select>
            </mat-form-field>

            <!-- Token mode -->
            @if (authMode() === 'token') {
              <mat-form-field class="full-width" appearance="outline">
                <mat-label>API Token</mat-label>
                <input
                  matInput
                  type="password"
                  formControlName="token"
                  placeholder="Paste your Mist API token"
                  autocomplete="current-password"
                />
                <mat-icon matSuffix>key</mat-icon>
              </mat-form-field>
            }

            <!-- Credentials mode -->
            @if (authMode() === 'credentials') {
              <mat-form-field class="full-width" appearance="outline">
                <mat-label>Email</mat-label>
                <input
                  matInput
                  type="email"
                  formControlName="email"
                  placeholder="you@example.com"
                  autocomplete="username"
                />
                <mat-icon matSuffix>email</mat-icon>
              </mat-form-field>
              <mat-form-field class="full-width" appearance="outline">
                <mat-label>Password</mat-label>
                <input
                  matInput
                  type="password"
                  formControlName="password"
                  autocomplete="current-password"
                />
                <mat-icon matSuffix>lock</mat-icon>
              </mat-form-field>
            }

            @if (errorMsg()) {
              <div class="error-msg">{{ errorMsg() }}</div>
            }

            @if (loading()) {
              <div class="spinner-row">
                <mat-spinner diameter="24"></mat-spinner>
                <span>Connecting...</span>
              </div>
            } @else {
              <button
                mat-flat-button
                color="primary"
                class="full-width"
                type="submit"
                [disabled]="form.invalid"
              >
                Connect
              </button>
            }
          </form>
        </mat-card-content>
      </mat-card>
    </div>
  `,
})
export class LoginComponent {
  @Output() authenticated = new EventEmitter<AuthInfo>();

  private fb = inject(FormBuilder);
  private api = inject(ApiService);

  clouds = CLOUDS;
  authMode = signal<'token' | 'credentials'>('token');
  loading = signal(false);
  errorMsg = signal('');

  form = this.fb.group({
    cloud: ['global_01', Validators.required],
    token: [''],
    email: [''],
    password: [''],
  });

  setMode(mode: 'token' | 'credentials'): void {
    this.authMode.set(mode);
    this.errorMsg.set('');
    // Reset validation fields
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

    const body =
      mode === 'token'
        ? { auth_type: 'token', token: token?.trim(), cloud }
        : { auth_type: 'credentials', email: email?.trim(), password, cloud };

    this.loading.set(true);
    this.api.post<AuthVerifyResponse>('auth/verify', body).subscribe({
      next: (res) => {
        this.loading.set(false);
        const cloudVal = cloud ?? 'global_01';
        if (mode === 'token') {
          this.authenticated.emit({
            user_id: res.user_id,
            user_email: res.user_email,
            orgs: res.orgs,
            cloud: cloudVal,
            auth_type: 'token',
            token: token?.trim(),
          });
        } else {
          this.authenticated.emit({
            user_id: res.user_id,
            user_email: res.user_email,
            orgs: res.orgs,
            cloud: cloudVal,
            auth_type: 'credentials',
            email: email?.trim(),
            password: password ?? '',
          });
        }
      },
      error: (err) => {
        this.loading.set(false);
        const msg =
          err?.error?.detail ?? err?.error?.message ?? 'Authentication failed. Please try again.';
        this.errorMsg.set(msg as string);
      },
    });
  }
}
