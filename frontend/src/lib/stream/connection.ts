/**
 * One WebSocket connection to a simulation's stream, independent of React.
 *
 * - Messages are delivered in batches (at most one per FLUSH_MS), so a burst of updates from
 *   one runner tick becomes one state change and one render.
 * - A dropped connection is retried with exponential backoff. The server starts every
 *   connection with a snapshot, which replaces whatever the client held, so nothing is lost.
 * - It is not retried when the server says the simulation or a symbol does not exist (close
 *   codes 4404 and 4400) or that the simulation was deleted (a `closed` message).
 */
import { parseMessage, type StreamMessage } from "./messages";

export type ConnectionState = "idle" | "connecting" | "live" | "reconnecting" | "closed";

export const FLUSH_MS = 33;
export const MAX_RETRY_MS = 5_000;
const FINAL_CLOSE_CODES = new Set([4400, 4404]);

export interface ConnectionCallbacks {
  onMessages(messages: StreamMessage[]): void;
  onState(state: ConnectionState, detail: string | null): void;
  /** A frame that is not a known message (reported, then ignored). */
  onInvalid?(data: unknown): void;
}

/** The parts of the browser's WebSocket used here, so tests can substitute a fake. */
export interface SocketLike {
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number; reason: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  close(code?: number, reason?: string): void;
}
export type SocketFactory = (url: string) => SocketLike;

const browserSocket: SocketFactory = (url) => new WebSocket(url) as unknown as SocketLike;

export function retryDelay(attempt: number): number {
  return Math.min(MAX_RETRY_MS, 250 * 2 ** attempt);
}

export class StreamConnection {
  private socket: SocketLike | null = null;
  private buffer: StreamMessage[] = [];
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private attempt = 0;
  private stopped = false;
  private deleted = false;

  constructor(
    private readonly url: string,
    private readonly callbacks: ConnectionCallbacks,
    private readonly createSocket: SocketFactory = browserSocket,
  ) {}

  start(): void {
    this.stopped = false;
    this.open();
  }

  /** Closes the connection for good; pending messages are dropped. */
  stop(): void {
    this.stopped = true;
    if (this.flushTimer !== null) clearTimeout(this.flushTimer);
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.flushTimer = this.retryTimer = null;
    this.buffer = [];
    const socket = this.socket;
    this.socket = null;
    socket?.close(1000, "client closed");
  }

  private open(): void {
    this.retryTimer = null;
    this.callbacks.onState(this.attempt === 0 ? "connecting" : "reconnecting", null);
    const socket = this.createSocket(this.url);
    this.socket = socket;
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      const message = parseMessage(event.data);
      if (message === null) {
        this.callbacks.onInvalid?.(event.data);
        return;
      }
      if (message.type === "snapshot") {
        this.attempt = 0;
        this.callbacks.onState("live", null);
      } else if (message.type === "closed") {
        this.deleted = true;
      }
      this.buffer.push(message);
      this.flushTimer ??= setTimeout(() => this.flush(), FLUSH_MS);
    };
    socket.onerror = () => {
      // A close event always follows; the retry is decided there.
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.flush();
      if (this.stopped) return;
      if (this.deleted || FINAL_CLOSE_CODES.has(event.code)) {
        this.callbacks.onState("closed", event.reason || "the simulation was deleted");
        return;
      }
      this.callbacks.onState("reconnecting", event.reason || null);
      this.retryTimer = setTimeout(() => this.open(), retryDelay(this.attempt));
      this.attempt += 1;
    };
  }

  private flush(): void {
    if (this.flushTimer !== null) clearTimeout(this.flushTimer);
    this.flushTimer = null;
    if (this.buffer.length === 0) return;
    const batch = this.buffer;
    this.buffer = [];
    this.callbacks.onMessages(batch);
  }
}
