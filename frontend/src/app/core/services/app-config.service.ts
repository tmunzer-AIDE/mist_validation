import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';

interface AppConfig {
  github_url?: string;
  docker_url?: string;
}

@Injectable({ providedIn: 'root' })
export class AppConfigService {
  private api = inject(ApiService);

  private _config = signal<AppConfig>({});
  readonly config = this._config.asReadonly();
  readonly githubUrl = computed(() => this._config().github_url ?? '');
  readonly dockerUrl = computed(() => this._config().docker_url ?? '');

  load(): void {
    this.api.get<AppConfig>('config').subscribe({
      next: (cfg) => this._config.set(cfg ?? {}),
      error: () => this._config.set({}),
    });
  }
}
