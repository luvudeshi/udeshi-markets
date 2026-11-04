/**
 * Rendering geometry for the distribution chart: axis ticks and the horizontal spread of the
 * run dots. Pixel placement only; the values plotted are the backend's.
 */

export interface Axis {
  /** The axis range (rounded out to whole ticks). */
  low: number;
  high: number;
  ticks: number[];
  /** Decimal places that show every tick distinctly. */
  decimals: number;
}

/** About ``count`` evenly spaced, round ticks covering [min, max]. */
export function niceAxis(min: number, max: number, count = 5): Axis {
  let low = min;
  let high = max;
  if (!(Number.isFinite(low) && Number.isFinite(high))) {
    return { low: 0, high: 1, ticks: [0, 0.5, 1], decimals: 1 };
  }
  if (high < low) [low, high] = [high, low];
  if (high === low) {
    const pad = Math.abs(low) * 0.05 || 1;
    low -= pad;
    high += pad;
  }
  const raw = (high - low) / Math.max(1, count);
  const power = 10 ** Math.floor(Math.log10(raw));
  const multiple = [1, 2, 2.5, 5, 10].find((m) => m * power >= raw) ?? 10;
  const step = multiple * power;
  const first = Math.floor(low / step);
  const last = Math.ceil(high / step);
  const decimals = Math.max(0, -Math.floor(Math.log10(step)) + (multiple === 2.5 ? 1 : 0));
  const ticks: number[] = [];
  for (let i = first; i <= last; i++) ticks.push(Number((i * step).toFixed(decimals + 2)));
  return { low: first * step, high: last * step, ticks, decimals };
}

/** A fixed offset in [-0.5, 0.5) for a run's dot, from its seed: the same run always lands in
 * the same place, and neighbouring seeds spread out (a 32-bit integer hash). */
export function jitter(seed: number): number {
  let h = ((seed % 4294967296) + 4294967296) % 4294967296;
  h = Math.imul(h ^ (h >>> 16), 0x7feb352d);
  h = Math.imul(h ^ (h >>> 15), 0x846ca68b);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296 - 0.5;
}
