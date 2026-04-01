import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';
import type { AuthInfo } from '../../app.component';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);
  private base = '/api';

  get<T>(path: string, headers?: Record<string, string>): Observable<T> {
    return this.http.get<T>(`${this.base}/${path}`, { headers: new HttpHeaders(headers ?? {}) });
  }

  post<T>(path: string, body: unknown, headers?: Record<string, string>): Observable<T> {
    return this.http.post<T>(`${this.base}/${path}`, body, {
      headers: new HttpHeaders(headers ?? {}),
    });
  }

  getBlob(path: string, headers?: Record<string, string>): Observable<Blob> {
    return this.http.get(`${this.base}/${path}`, {
      responseType: 'blob',
      headers: new HttpHeaders(headers ?? {}),
    });
  }

  /** Build Mist credential headers from AuthInfo (token OR email+password). */
  mistAuthHeaders(auth: AuthInfo): Record<string, string> {
    if (auth.auth_type === 'token' && auth.token) {
      return { 'X-Mist-Token': auth.token, 'X-Mist-Cloud': auth.cloud };
    }
    return {
      'X-Mist-Email': auth.email ?? '',
      'X-Mist-Password': auth.password ?? '',
      'X-Mist-Cloud': auth.cloud,
    };
  }
}
