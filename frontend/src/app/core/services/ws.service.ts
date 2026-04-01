import { Injectable } from '@angular/core';
import { Observable, Subject, filter, share } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class WsService {
  private socket: WebSocket | null = null;
  private messages$ = new Subject<Record<string, unknown>>();
  private state: 'disconnected' | 'connecting' | 'connected' = 'disconnected';

  private connect(): void {
    if (this.state !== 'disconnected') return;
    this.state = 'connecting';
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.socket = new WebSocket(`${proto}://${location.host}/ws`);
    this.socket.onopen = () => {
      this.state = 'connected';
    };
    this.socket.onmessage = (event) => {
      try {
        this.messages$.next(JSON.parse(event.data as string) as Record<string, unknown>);
      } catch {
        // ignore malformed messages
      }
    };
    this.socket.onclose = () => {
      this.state = 'disconnected';
      this.socket = null;
    };
    this.socket.onerror = () => {
      this.state = 'disconnected';
      this.socket = null;
    };
  }

  subscribe(channel: string): void {
    this.connect();
    const send = () =>
      this.socket?.send(JSON.stringify({ type: 'subscribe', channel }));
    if (this.state === 'connected') {
      send();
    } else {
      this.socket?.addEventListener('open', send, { once: true });
    }
  }

  unsubscribe(channel: string): void {
    this.socket?.send(JSON.stringify({ type: 'unsubscribe', channel }));
  }

  channel$(channel: string): Observable<Record<string, unknown>> {
    return this.messages$.pipe(
      filter((msg) => msg['channel'] === channel),
      share(),
    );
  }
}
