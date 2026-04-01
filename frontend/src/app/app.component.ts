import { Component, signal } from '@angular/core';
import { LoginComponent } from './features/login/login.component';
import { SiteSelectorComponent } from './features/site-selector/site-selector.component';
import { ReportViewComponent } from './features/report-view/report-view.component';

type AppState = 'login' | 'site_selector' | 'report';

export interface AuthInfo {
  user_id: string;
  user_email: string;
  orgs: { id: string; name: string }[];
  cloud: string;
  auth_type: 'token' | 'credentials';
  token?: string;
  email?: string;
  password?: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [LoginComponent, SiteSelectorComponent, ReportViewComponent],
  template: `
    @switch (appState()) {
      @case ('login') {
        <app-login (authenticated)="onAuthenticated($event)" />
      }
      @case ('site_selector') {
        <app-site-selector
          [authInfo]="authInfo()!"
          [selectedOrg]="selectedOrg()"
          (orgSelected)="selectedOrg.set($event)"
          (reportStarted)="onReportStarted($event)"
        />
      }
      @case ('report') {
        <app-report-view
          [jobId]="activeJobId()!"
          [authInfo]="authInfo()!"
          (back)="appState.set('site_selector')"
        />
      }
    }
  `,
})
export class AppComponent {
  appState = signal<AppState>('login');
  authInfo = signal<AuthInfo | null>(null);
  selectedOrg = signal<{ id: string; name: string } | null>(null);
  activeJobId = signal<string | null>(null);

  onAuthenticated(info: AuthInfo): void {
    this.authInfo.set(info);
    if (info.orgs.length === 1) {
      this.selectedOrg.set(info.orgs[0]);
    }
    this.appState.set('site_selector');
  }

  onReportStarted(jobId: string): void {
    this.activeJobId.set(jobId);
    this.appState.set('report');
  }
}
