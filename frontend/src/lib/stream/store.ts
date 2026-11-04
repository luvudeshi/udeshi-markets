/**
 * The terminal's state: what the stream delivered (see reducer.ts), the connection's state,
 * and which simulation and symbol the user is looking at.
 */
import { create } from "zustand";

import type { ConnectionState } from "./connection";
import type { StreamMessage } from "./messages";
import { EMPTY_STREAM, reduceAll, type MarketState, type StreamState } from "./reducer";

export interface TerminalStore extends StreamState {
  simulationId: string | null;
  connection: ConnectionState;
  connectionDetail: string | null;
  selectedSymbol: string | null;

  /** Switches to another simulation (or none): its stream state starts empty. */
  setSimulation(id: string | null): void;
  selectSymbol(symbol: string): void;
  apply(messages: StreamMessage[]): void;
  setConnection(state: ConnectionState, detail: string | null): void;
}

export const useTerminal = create<TerminalStore>()((set) => ({
  ...EMPTY_STREAM,
  simulationId: null,
  connection: "idle",
  connectionDetail: null,
  selectedSymbol: null,

  setSimulation: (id) =>
    set((state) =>
      state.simulationId === id
        ? state
        : { ...EMPTY_STREAM, simulationId: id, connection: "idle", connectionDetail: null },
    ),
  selectSymbol: (symbol) => set({ selectedSymbol: symbol }),
  apply: (messages) =>
    set((state) => {
      const next = reduceAll(state, messages);
      const keep = state.selectedSymbol !== null && next.symbols.includes(state.selectedSymbol);
      return { ...next, selectedSymbol: keep ? state.selectedSymbol : (next.symbols[0] ?? null) };
    }),
  setConnection: (connection, detail) => set({ connection, connectionDetail: detail }),
}));

/** The selected symbol's market state (undefined before the first snapshot). */
export function useSelectedMarket(): MarketState | undefined {
  return useTerminal((state) =>
    state.selectedSymbol === null ? undefined : state.markets[state.selectedSymbol],
  );
}
