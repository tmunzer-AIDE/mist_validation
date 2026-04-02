import { Component, signal } from '@angular/core';
import { LoginComponent } from './features/login/login.component';
import { SiteSelectorComponent } from './features/site-selector/site-selector.component';
import { ReportViewComponent } from './features/report-view/report-view.component';

type AppState = 'login' | 'site_selector' | 'report';

export interface AuthInfo {
  user_email: string;
  token_name: string;
  orgs: { id: string; name: string; role: string }[];
  cloud: string;
  host: string;
  method: 'token' | 'credentials';
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [LoginComponent, SiteSelectorComponent, ReportViewComponent],
  templateUrl: './app.component.html',
})
export class AppComponent {
  appState = signal<AppState>('login');
  authInfo = signal<AuthInfo | null>(null);
  selectedOrg = signal<{ id: string; name: string; role?: string } | null>(null);
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
