import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);
  private base = '/api';

  get<T>(path: string, headers?: Record<string, string>): Observable<T> {
    return this.http.get<T>(`${this.base}/${path}`, {
      headers: new HttpHeaders(headers ?? {}),
      withCredentials: true,
    });
  }

  post<T>(path: string, body: unknown, headers?: Record<string, string>): Observable<T> {
    return this.http.post<T>(`${this.base}/${path}`, body, {
      headers: new HttpHeaders(headers ?? {}),
      withCredentials: true,
    });
  }

  delete(path: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${path}`, { withCredentials: true });
  }

  getBlob(path: string, headers?: Record<string, string>): Observable<Blob> {
    return this.http.get(`${this.base}/${path}`, {
      responseType: 'blob',
      headers: new HttpHeaders(headers ?? {}),
      withCredentials: true,
    });
  }
}
