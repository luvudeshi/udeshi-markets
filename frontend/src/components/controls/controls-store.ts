/**
 * State the controls share: the new-simulation dialog (opened from the top bar and from the
 * no-simulation overlay), a counter that tells simulation lists to refresh, and the error toast.
 */
import { create } from "zustand";

import { describeError, type ErrorDescription } from "@/lib/errors";

export interface Toast extends ErrorDescription {
  id: number;
}

interface ControlsStore {
  dialogOpen: boolean;
  /** Incremented after a simulation is created or deleted. */
  listVersion: number;
  toast: Toast | null;

  openDialog(): void;
  closeDialog(): void;
  simulationsChanged(): void;
  /** Shows ``error`` in the toast (replacing the previous one). */
  notify(error: unknown): void;
  /** Hides toast ``id``, unless a newer one has replaced it. */
  dismiss(id: number): void;
}

let nextToastId = 1;

export const useControls = create<ControlsStore>()((set) => ({
  dialogOpen: false,
  listVersion: 0,
  toast: null,

  openDialog: () => set({ dialogOpen: true }),
  closeDialog: () => set({ dialogOpen: false }),
  simulationsChanged: () => set((state) => ({ listVersion: state.listVersion + 1 })),
  notify: (error) => {
    console.error(error);
    set({ toast: { id: nextToastId++, ...describeError(error) } });
  },
  dismiss: (id) => set((state) => (state.toast?.id === id ? { toast: null } : state)),
}));
