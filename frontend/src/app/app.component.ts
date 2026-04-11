import { Component, HostListener, OnInit, signal } from '@angular/core';
import { LoginComponent } from './features/login/login.component';
import { SiteSelectorComponent } from './features/site-selector/site-selector.component';
import { ReportViewComponent } from './features/report-view/report-view.component';
import { OrgReportViewComponent } from './features/org-report-view/org-report-view.component';
import { ValidationReferenceComponent } from './features/validation-reference/validation-reference.component';

type AppState = 'login' | 'site_selector' | 'report' | 'org_report' | 'validation_reference';

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
  imports: [LoginComponent, SiteSelectorComponent, ReportViewComponent, OrgReportViewComponent, ValidationReferenceComponent],
  templateUrl: './app.component.html',
})
export class AppComponent implements OnInit {
  appState = signal<AppState>('login');
  authInfo = signal<AuthInfo | null>(null);
  selectedOrg = signal<{ id: string; name: string; role?: string } | null>(null);
  activeJobId = signal<string | null>(null);

  private skipPush = false;

  ngOnInit(): void {
    history.replaceState({ state: 'login' }, '');
  }

  @HostListener('window:popstate', ['$event'])
  onPopState(event: PopStateEvent): void {
    const target = event.state?.state as AppState | undefined;
    if (!target || !this.authInfo()) return;

    this.skipPush = true;
    this.appState.set(target);
    this.skipPush = false;
  }

  pushState(state: AppState): void {
    this.appState.set(state);
    if (!this.skipPush) {
      history.pushState({ state }, '');
    }
  }

  showSiteSelector(): void {
    this.pushState('site_selector');
  }

  onAuthenticated(info: AuthInfo): void {
    this.authInfo.set(info);
    if (info.orgs.length === 1) {
      this.selectedOrg.set(info.orgs[0]);
    }
    this.pushState('site_selector');
  }

  onReportStarted(event: { id: string; scope: string }): void {
    this.activeJobId.set(event.id);
    this.pushState(event.scope === 'org' ? 'org_report' : 'report');
  }
}
