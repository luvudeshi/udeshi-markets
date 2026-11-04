import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  FLUSH_MS,
  StreamConnection,
  retryDelay,
  type ConnectionState,
  type SocketLike,
} from "./connection";
import type { StreamMessage } from "./messages";

class FakeSocket implements SocketLike {
  onmessage: SocketLike["onmessage"] = null;
  onclose: SocketLike["onclose"] = null;
  onerror: SocketLike["onerror"] = null;
  closedWith: number | null = null;
  constructor(readonly url: string) {}
  close(code?: number) {
    this.closedWith = code ?? 1000;
  }
  receive(message: object | string) {
    this.onmessage?.({ data: typeof message === "string" ? message : JSON.stringify(message) });
  }
  drop(code: number, reason = "") {
    this.onclose?.({ code, reason });
  }
}

const SNAPSHOT = { type: "snapshot", simulation: {}, markets: [], agents: [], news: [] };

function harness() {
  const sockets: FakeSocket[] = [];
  const batches: StreamMessage[][] = [];
  const states: [ConnectionState, string | null][] = [];
  const invalid: unknown[] = [];
  const connection = new StreamConnection(
    "ws://test/stream",
    {
      onMessages: (messages) => batches.push(messages),
      onState: (state, detail) => states.push([state, detail]),
      onInvalid: (data) => invalid.push(data),
    },
    (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
  );
  return { connection, sockets, batches, states, invalid };
}

describe("StreamConnection", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("batches messages and goes live on the snapshot", () => {
    const { connection, sockets, batches, states } = harness();
    connection.start();
    expect(states).toEqual([["connecting", null]]);
    const socket = sockets[0]!;
    socket.receive(SNAPSHOT);
    socket.receive({ type: "simulation", state: "running" });
    expect(states.at(-1)).toEqual(["live", null]);
    expect(batches).toEqual([]); // Not yet: one flush per FLUSH_MS.
    vi.advanceTimersByTime(FLUSH_MS);
    expect(batches).toHaveLength(1);
    expect(batches[0]?.map((m) => m.type)).toEqual(["snapshot", "simulation"]);
  });

  it("reports frames that are not known messages", () => {
    const { connection, sockets, batches, invalid } = harness();
    connection.start();
    sockets[0]!.receive("not json");
    sockets[0]!.receive({ type: "mystery" });
    vi.advanceTimersByTime(FLUSH_MS);
    expect(invalid).toHaveLength(2);
    expect(batches).toEqual([]);
  });

  it("reconnects with backoff after a drop, and delivers what arrived first", () => {
    const { connection, sockets, batches, states } = harness();
    connection.start();
    sockets[0]!.receive(SNAPSHOT);
    sockets[0]!.drop(1006);
    expect(batches).toHaveLength(1); // Flushed on close.
    expect(states.at(-1)).toEqual(["reconnecting", null]);
    vi.advanceTimersByTime(retryDelay(0) - 1);
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(2);
    sockets[1]!.drop(1006);
    vi.advanceTimersByTime(retryDelay(1));
    expect(sockets).toHaveLength(3);
    expect(retryDelay(10)).toBe(5_000);
  });

  it("stops for good when the simulation is unknown or deleted", () => {
    const unknown = harness();
    unknown.connection.start();
    unknown.sockets[0]!.drop(4404, "no simulation with id 'x'");
    vi.advanceTimersByTime(60_000);
    expect(unknown.sockets).toHaveLength(1);
    expect(unknown.states.at(-1)).toEqual(["closed", "no simulation with id 'x'"]);

    const deleted = harness();
    deleted.connection.start();
    deleted.sockets[0]!.receive(SNAPSHOT);
    deleted.sockets[0]!.receive({ type: "closed" });
    deleted.sockets[0]!.drop(1000);
    vi.advanceTimersByTime(60_000);
    expect(deleted.sockets).toHaveLength(1);
    expect(deleted.states.at(-1)?.[0]).toBe("closed");
  });

  it("stop() closes the socket and never reconnects", () => {
    const { connection, sockets, batches } = harness();
    connection.start();
    sockets[0]!.receive(SNAPSHOT);
    connection.stop();
    expect(sockets[0]!.closedWith).toBe(1000);
    sockets[0]!.drop(1000); // The browser's close event arrives afterwards.
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(1);
    expect(batches).toEqual([]); // Pending messages are dropped.
  });
});
