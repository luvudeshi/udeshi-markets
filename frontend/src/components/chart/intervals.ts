/**
 * Chart intervals. The backend aggregates bars, and only to whole multiples of the
 * simulation's bar interval, so those are the only choices offered.
 */

export const STANDARD_INTERVALS = [1, 5, 15, 60, 300] as const;

/** Nanoseconds, as the backend compares them: avoids 0.1 + 0.2 style surprises. */
function nanos(seconds: number): number {
  return Math.round(seconds * 1e9);
}

/** The simulation's own bar interval, then every standard interval that is a whole multiple of
 * it, ascending. */
export function intervalOptions(barIntervalSeconds: number): number[] {
  const base = nanos(barIntervalSeconds);
  if (base <= 0) return [];
  const options = new Set<number>([barIntervalSeconds]);
  for (const seconds of STANDARD_INTERVALS) {
    const candidate = nanos(seconds);
    if (candidate > base && candidate % base === 0) options.add(seconds);
  }
  return [...options].sort((a, b) => a - b);
}

/** The interval shown first: one second when offered (readable at any speed), else the
 * simulation's own. */
export function defaultInterval(options: readonly number[]): number | null {
  if (options.length === 0) return null;
  return options.includes(1) ? 1 : (options[0] ?? null);
}

/** 250ms, 1s, 15s, 1m, 5m, 1h. */
export function intervalLabel(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${seconds / 60}m`;
  return `${seconds / 3600}h`;
}
