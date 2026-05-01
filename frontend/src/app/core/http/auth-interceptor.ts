import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';
import { AuthEventsService } from '../services/auth-events.service';

/**
 * Catches 401s from any /api/* request and notifies the auth-events service
 * so the shell can reset its auth state and route back to login. The error
 * is re-thrown so the originating component can still react (e.g. the login
 * form keeps displaying its "wrong credentials" message on a failed login).
 *
 * The interceptor only acts on requests to our own API (path starts with
 * `/api/`) — it must not nuke the session on a stray 401 from a third-party
 * URL pulled in by an HTML artifact, etc.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const events = inject(AuthEventsService);
  return next(req).pipe(
    catchError((err: unknown) => {
      if (
        err instanceof HttpErrorResponse &&
        err.status === 401 &&
        req.url.startsWith('/api/')
      ) {
        events.notifyUnauthorized();
      }
      return throwError(() => err);
    }),
  );
};
