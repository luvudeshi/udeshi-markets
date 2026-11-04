/**
 * Where things go on the chart's time axis. lightweight-charts spaces bars evenly by index, so
 * anything timed between bar starts (a trade, a news item) is placed relative to the bar that
 * contains it. Rendering only: no prices are computed here.
 */

export interface BarSlot {
  /** Index of the bar containing the time (it may lie past the last loaded bar). */
  index: number;
  /** How far into that bar the time falls, in [0, 1). */
  fraction: number;
}

/**
 * The bar containing ``time``, given the loaded bars' start times (ascending, ``interval``
 * apart, as the backend produces them). Times after the last bar continue at the same spacing
 * (the bar still forming); times before the first bar have no slot.
 */
export function slotOf(starts: readonly number[], interval: number, time: number): BarSlot | null {
  const first = starts[0];
  if (first === undefined || interval <= 0 || time < first) return null;
  // The last bar whose start is at or before the time.
  let low = 0;
  let high = starts.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if ((starts[middle] as number) <= time) low = middle;
    else high = middle - 1;
  }
  const start = starts[low] as number;
  const offset = (time - start) / interval;
  const whole = Math.floor(offset);
  // A time inside a bar (or past the last one): beyond the loaded bars, count on.
  const index = low === starts.length - 1 ? low + whole : low;
  const fraction = low === starts.length - 1 ? offset - whole : Math.min(offset, 1 - 1e-9);
  return { index, fraction };
}

/** The start time of the loaded bar containing ``time``, or null if no loaded bar does. */
export function barStartOf(
  starts: readonly number[],
  interval: number,
  time: number,
): number | null {
  const slot = slotOf(starts, interval, time);
  if (slot === null || slot.index >= starts.length) return null;
  return starts[slot.index] ?? null;
}

/**
 * The history, followed by the streamed bars that closed after its last one. Both come from
 * the backend's recorder at the simulation's own bar interval; the stream is simply newer
 * between (throttled) history requests.
 */
export function withNewerBars<T extends { start_seconds: number }>(
  history: T[],
  streamed: readonly T[],
): T[] {
  const last = history.at(-1)?.start_seconds ?? Number.NEGATIVE_INFINITY;
  const newer = streamed.filter((bar) => bar.start_seconds > last);
  return newer.length === 0 ? history : [...history, ...newer]; // Same array when nothing is new.
}

/** How many of ``times`` fall on or after the first loaded bar, and so have a place on the
 * chart. */
export function countPlaced(starts: readonly number[], interval: number, times: readonly number[]) {
  let count = 0;
  for (const time of times) if (slotOf(starts, interval, time) !== null) count += 1;
  return count;
}
