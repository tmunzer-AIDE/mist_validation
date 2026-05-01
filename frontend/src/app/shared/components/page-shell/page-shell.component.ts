import { Component, EventEmitter, Output, inject, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { AppConfigService } from '../../../core/services/app-config.service';

export type ShellRoute = 'site_selector' | 'reports' | 'validation_reference';

interface NavItem {
  id: ShellRoute;
  label: string;
  icon: string;
}

interface BreadcrumbItem {
  label: string;
  route?: ShellRoute;
}

@Component({
  selector: 'app-page-shell',
  standalone: true,
  imports: [MatIconModule],
  templateUrl: './page-shell.component.html',
  styleUrl: './page-shell.component.scss',
})
export class PageShellComponent {
  title = input.required<string>();
  titleSuffix = input<string>('');
  subtitle = input<string>('');
  breadcrumb = input<BreadcrumbItem[]>([]);
  activeRoute = input<ShellRoute>('site_selector');
  userLabel = input<string>('');

  @Output() navigate = new EventEmitter<ShellRoute>();

  private cfg = inject(AppConfigService);

  navItems: NavItem[] = [
    { id: 'site_selector', label: 'Run validation', icon: 'play_arrow' },
    { id: 'reports', label: 'Reports', icon: 'history' },
    { id: 'validation_reference', label: 'Reference', icon: 'menu_book' },
  ];

  githubUrl = this.cfg.githubUrl;
  dockerUrl = this.cfg.dockerUrl;

  onNav(id: ShellRoute): void {
    this.navigate.emit(id);
  }

  onCrumb(item: BreadcrumbItem): void {
    if (item.route) this.navigate.emit(item.route);
  }
}
