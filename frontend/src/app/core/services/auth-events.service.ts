import { Injectable, signal } from '@angular/core';

/**
 * Bridges the HTTP layer to the app shell when the server reports the session
 * is no longer valid (HTTP 401). The interceptor calls `notifyUnauthorized()`;
 * the app shell watches `unauthorized` and resets its auth state + routes back
 * to login.
 *
 * Modeled as a counter rather than a boolean so consumers can react to repeat
 * 401s (e.g. session expired again after a re-login attempt) — every increment
 * fires the effect.
 */
@Injectable({ providedIn: 'root' })
export class AuthEventsService {
  readonly unauthorized = signal(0);

  notifyUnauthorized(): void {
    this.unauthorized.update((n) => n + 1);
  }
}
